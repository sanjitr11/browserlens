"""Token budget — count tokens and truncate output to fit within a limit."""

from __future__ import annotations

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False


class TokenBudget:
    """
    Counts tokens in a string and truncates text to fit within a budget.
    Falls back to a character-based heuristic when tiktoken isn't available.
    """

    # Rough chars-per-token ratio for fallback
    _CHARS_PER_TOKEN = 4

    def count(self, text: str) -> int:
        if _TIKTOKEN_AVAILABLE:
            return len(_enc.encode(text))
        return max(1, len(text) // self._CHARS_PER_TOKEN)

    def truncate(self, text: str, max_tokens: int) -> tuple[str, bool]:
        """
        Truncate text to fit within max_tokens.
        Returns (truncated_text, was_truncated).
        """
        if self.count(text) <= max_tokens:
            return text, False

        if _TIKTOKEN_AVAILABLE:
            tokens = _enc.encode(text)
            truncated = _enc.decode(tokens[:max_tokens])
        else:
            max_chars = max_tokens * self._CHARS_PER_TOKEN
            truncated = text[:max_chars]

        return truncated + "\n[... truncated to fit token budget ...]", True

    def fits(self, text: str, max_tokens: int) -> bool:
        return self.count(text) <= max_tokens
