from types import SimpleNamespace

from api.services.llm_service import _extract_message_text


def test_extract_message_text_from_standard_content():
    message = SimpleNamespace(content="Sell AAPL 150P only if evidence supports it.")

    assert _extract_message_text(message) == "Sell AAPL 150P only if evidence supports it."


def test_extract_message_text_from_reasoning_content():
    message = SimpleNamespace(content=None, reasoning_content="Consider rolling the threatened short call.")

    assert _extract_message_text(message) == "Consider rolling the threatened short call."


def test_extract_message_text_from_content_parts():
    message = SimpleNamespace(content=[{"type": "text", "text": "Close covered call at 50% profit."}])

    assert _extract_message_text(message) == "Close covered call at 50% profit."


def test_extract_message_text_from_provider_mapping():
    class ProviderMessage:
        content = None

        def model_dump(self):
            return {"additional_kwargs": {"reasoning": "No new open suggested because evidence is incomplete."}}

    assert _extract_message_text(ProviderMessage()) == "No new open suggested because evidence is incomplete."


def test_extract_message_text_returns_empty_for_no_displayable_text():
    assert _extract_message_text(SimpleNamespace(content="   ")) == ""
