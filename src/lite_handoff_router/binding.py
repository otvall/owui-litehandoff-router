"""Bind a Tool's effective context through OWUI's native callable refresh."""

import inspect


async def bind_tool_context(adapter, tool, messages, check=None):
    function = tool["callable"]
    original = getattr(function, "__function__", function)
    extra = getattr(function, "__extra_params__", None)
    signature = inspect.signature(original)
    if check is None and "__messages__" not in signature.parameters:
        return tool

    async def protected(*args, **kwargs):
        if "__messages__" in signature.parameters:
            kwargs["__messages__"] = messages
        if check is not None:
            arguments = signature.bind_partial(*args, **kwargs).arguments
            await check(arguments)
        result = original(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    protected.__name__ = getattr(original, "__name__", "tool")
    protected.__signature__ = signature
    try:
        protected.__annotations__ = inspect.get_annotations(original, eval_str=True)
    except (NameError, TypeError):
        protected.__annotations__ = getattr(original, "__annotations__", {})
    if extra is not None:
        protected = await adapter.bind(protected, {**extra, "__messages__": messages})
    return {**tool, "callable": protected}
