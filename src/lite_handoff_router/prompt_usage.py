"""Track the last prompt sample per executor and model on the active branch."""


def prompt_token_count(usage):
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "input_tokens", "prompt_eval_count"):
            value = usage.get(key)
            if type(value) is int and value > 0:
                return value
    return None


def usage_slot(model_id, agent_id):
    return ("agent_usage", (agent_id, model_id)) if agent_id else ("router_usage", model_id)


def last_prompt_tokens(state, model_id, *, agent_id, summary_key):
    bucket, key = usage_slot(model_id, agent_id)
    sample = state.get(bucket, {}).get(key, state.get("historical_" + bucket, {}).get(key))
    if sample and "summary_key" in sample and sample["summary_key"] == summary_key:
        return prompt_token_count(sample)
    return None


def remember_prompt_usage(state, model_id, tokens, *, agent_id, summary_key):
    bucket, key = usage_slot(model_id, agent_id)
    state.setdefault(bucket, {})[key] = {"prompt_tokens": tokens, "summary_key": summary_key}


def restore_prompt_usage(state, branch, *, resume):
    router_usage, agent_usage = {}, {}
    resumed_current = (isinstance(resume, (tuple, list)) and
                       tuple(resume[:2]) == (state["chat_id"], state["message_id"]))
    for message in branch:
        if message.get("role") != "assistant" or not isinstance(message.get("meta"), dict):
            continue
        meta = message["meta"]
        is_current = message.get("id") == state["message_id"]
        if not is_current:
            sample = meta.get("lite_router_usage")
            if isinstance(sample, dict) and isinstance(sample.get("model"), str):
                router_usage[sample["model"]] = sample
        if is_current and not resumed_current:
            continue
        samples = meta.get("lite_agent_usage")
        if isinstance(samples, list):
            for sample in samples:
                if (isinstance(sample, dict) and isinstance(sample.get("agent_id"), str)
                        and isinstance(sample.get("model"), str)):
                    agent_usage[sample["agent_id"], sample["model"]] = sample
    state["historical_router_usage"] = router_usage
    state["historical_agent_usage"] = agent_usage
