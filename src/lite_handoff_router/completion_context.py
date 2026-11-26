"""Prepare, compact, and observe the context for one Router or agent completion."""

from copy import deepcopy

from .agent_history_block import pack_agent_history
from .context_budget import check_context_budget, estimate_context_budget, input_budget, shorten_current_tool_results
from .context_policy import summary_model_id
from .observation import check_initial_stream, observe_response, routing_completion_status
from .prompt_usage import last_prompt_tokens, prompt_token_count, remember_prompt_usage
from .protocol import last_user
from .provider_errors import ProviderContextOverflow
from .shared_summary import SharedSummary, emit_context_status
from .routing_trace import record_routing_event


class CompletionContext:
    def __init__(self, adapter, request, user, state, draft, target, *, emitter=None):
        self.adapter, self.request, self.user = adapter, request, user
        self.state, self.draft, self.target = state, draft, target
        self.emitter = emitter
        self.recipient = target or state["router_id"]
        self.available = input_budget(state["context_policy"], draft)
        self.model_id = draft["model"]
        if target:
            info = (draft["metadata"].get("model") or {}).get("info") or {}
            self.model_id = info.get("base_model_id") or self.model_id
        self.packet = state.get("shared_summary_packet")
        self.summary = (SharedSummary(adapter, request, user, state, emitter=emitter)
                        if summary_model_id(state) else None)
        self.full_messages = deepcopy(draft["messages"]) if self.summary else None

    async def prepare(self):
        if self.summary:
            await self.summary.load(self.draft, self.packet)
            self.summary.apply(self.draft, self.packet)
        if self.draft.get("stream"):
            options = self.draft.get("stream_options")
            self.draft["stream_options"] = {
                **(options if isinstance(options, dict) else {}), "include_usage": True,
            }
        self.pack()
        await self.fit_budget(proactive=True)

    def proactive_trigger(self, estimate):
        policy = self.state["context_policy"]
        window = policy["context_window_tokens"]
        if window is None or not self.summary.turns:
            return None
        percent = policy["shared_summary_trigger_percent"]
        threshold = (window * percent + 99) // 100
        previous = last_prompt_tokens(
            self.state, self.model_id, agent_id=self.target, summary_key=self.summary.applied_summary_key())
        name = "Агент" if self.target else "Роутер"
        if previous is not None and previous >= threshold:
            return f"{name}, предыдущий вход {previous} токенов, порог {threshold} ({percent}%)"
        if estimate.total >= threshold:
            return f"{name}, оценка текущего входа {estimate.total} токенов, порог {threshold} ({percent}%)"
        return None

    async def compact(self, trigger):
        return self.summary is not None and await self.summary.extend(
            self.draft, self.packet, self.full_messages, trigger=trigger, pack=self.pack)

    def pack(self):
        if self.target and not self.state.get("worker"):
            pack_agent_history(
                self.draft["messages"], self.state, self.state.get("agent_history_question"),
                covered_turns=self.summary.applied_turns if self.summary else 0, packet=self.packet,
            )

    async def fit_budget(self, *, proactive=False):
        estimate = estimate_context_budget(self.draft)
        initial = estimate.total
        if proactive and self.summary and (self.available is None or estimate.total <= self.available):
            trigger = self.proactive_trigger(estimate)
            if trigger and await self.compact(trigger):
                estimate = estimate_context_budget(self.draft)
        if self.available is None:
            return
        while 0 < self.available < estimate.total and await self.compact(
            f"Бюджет запроса, оценка {estimate.total} токенов при лимите {self.available}",
        ):
            estimate = estimate_context_budget(self.draft)
        if 0 < self.available < estimate.total:
            estimate, _ = await self.shorten_results(estimate)
        self.check_budget(estimate, initial)

    def check_budget(self, estimate, initial):
        if self.available is not None:
            check_context_budget(estimate, self.available, recipient=self.recipient, before=initial,
                                 hidden_results=self.state.get("hidden_tool_result_count", 0))

    async def shorten_results(self, estimate):
        updated, count = shorten_current_tool_results(self.draft, self.state, estimate, self.available)
        if count:
            await emit_context_status(
                self.emitter, f"Из-за размера контекста сокращены результаты инструментов: {count}. "
                "Полные результаты остались в чате.", done=True, action="lite_tool_result_budget",
            )
        return updated, count

    async def retry_with_shorter_results(self):
        estimate = estimate_context_budget(self.draft)
        updated, count = await self.shorten_results(estimate)
        if count:
            self.check_budget(updated, estimate.total)
        return count

    async def send(self):
        response = await self.adapter.complete(self.request, self.draft, self.user)
        messages = self.draft["messages"]
        if self.summary or any(message.get("role") == "tool" for message in messages[last_user(messages) + 1:]):
            return await check_initial_stream(response)
        return response

    async def complete(self):
        try:
            response = await self.send()
        except ProviderContextOverflow:
            if await self.compact("Переполнение контекста основного запроса"):
                await self.fit_budget()
                try:
                    response = await self.send()
                except ProviderContextOverflow:
                    if not await self.retry_with_shorter_results():
                        raise
                    response = await self.send()
            else:
                if not await self.retry_with_shorter_results():
                    raise
                response = await self.send()
        executor = {"kind": "agent", "agent_id": self.target} if self.target else {"kind": "router"}
        generation = self.state.get("generation", 0) + 1
        summary_key = self.summary.applied_summary_key() if self.summary else None

        async def record_usage(usage):
            tokens = prompt_token_count(usage)
            if tokens is None and self.summary is None:
                return
            remember_prompt_usage(
                self.state, self.model_id, tokens, agent_id=self.target, summary_key=summary_key)
            await self.adapter.save_prompt_usage(
                self.state, self.user, self.model_id, tokens, summary_key=summary_key,
                agent_id=self.target, generation=generation)
            if tokens is None and self.summary:
                await self.summary.status("Провайдер не вернул свежий замер входных токенов.", done=True,
                                          action="lite_prompt_usage")

        async def record_outcome(status):
            await record_routing_event(self.adapter, self.state, self.user, "execution", status, self.target)

        usage_handler = record_usage if self.target or self.summary else None
        trace_handler = record_outcome if self.state.get("routing_trace") is not None else None
        if hasattr(response, "body_iterator"):
            return observe_response(response, self.state, executor, on_usage=usage_handler, on_finish=trace_handler)
        if usage_handler:
            await usage_handler(response.get("usage") if isinstance(response, dict) else None)
        observed = observe_response(response, self.state, executor)
        if trace_handler:
            status = routing_completion_status(response) if isinstance(response, dict) else None
            await trace_handler(status or "incomplete")
        return observed
