from lantern.mask import mask_text, validate_and_restore


def test_masks_tags_variables_and_format_codes_in_order():
    source = 'こんにちは、[player_name]。{w}今日は{b}いい{/b}天気。%(place)sだよ。%%そのまま %s'
    masked = mask_text(source)
    assert masked.tokens == (
        "⟪VAR000⟫",
        "⟪TAG000⟫",
        "⟪TAG001⟫",
        "⟪TAG002⟫",
        "⟪FMT000⟫",
        "⟪FMT001⟫",
        "⟪FMT002⟫",
    )
    assert masked.originals[0] == "[player_name]"
    assert masked.originals[1] == "{w}"
    assert masked.originals[4] == "%(place)s"
    assert "⟪VAR000⟫" in masked.text
    assert "[player_name]" not in masked.text
    assert "{w}" not in masked.text


def test_restore_roundtrip_and_rejects_broken_placeholders():
    source = "こんにちは、[player_name]。{w}今日はいい天気ね。"
    masked = mask_text(source)
    english = "Hello, ⟪VAR000⟫.⟪TAG000⟫The weather is nice today."
    assert masked.tokens == ("⟪VAR000⟫", "⟪TAG000⟫")
    restored = validate_and_restore(source, english, reject_unchanged_cjk=True)
    assert restored.ok
    assert restored.text == "Hello, [player_name].{w}The weather is nice today."

    swapped = masked.text.replace("⟪VAR000⟫", "⟪TAG000⟫").replace("⟪TAG000⟫", "⟪VAR000⟫")
    # The double replace collapses both to VAR; build an explicit swap instead.
    swapped = masked.text.replace("⟪VAR000⟫", "⟪TMP⟫").replace("⟪TAG000⟫", "⟪VAR000⟫").replace("⟪TMP⟫", "⟪TAG000⟫")
    bad = validate_and_restore(source, swapped, reject_unchanged_cjk=True)
    assert not bad.ok
    assert "placeholders" in (bad.error or "")

    missing = validate_and_restore(source, "Hello there.", reject_unchanged_cjk=True)
    assert not missing.ok

    echo = validate_and_restore(source, masked.text, reject_unchanged_cjk=True)
    assert not echo.ok
    assert "echoed" in (echo.error or "")


def test_escaped_braces_and_percent_are_kept():
    source = "得点は {{100}} 点[[1]]です。"
    masked = mask_text(source)
    assert "{{" not in masked.text
    assert "[[" not in masked.text
    result = validate_and_restore(source, "Score. " + masked.text, reject_unchanged_cjk=False)
    assert result.ok
    assert "{{100}}" in result.text
    assert "[[1]]" in result.text


def test_newline_count_is_checked():
    source = "一行目。\n二行目。"
    masked = mask_text(source)
    collapsed = masked.text.replace("\n", " ")
    result = validate_and_restore(source, collapsed, reject_unchanged_cjk=True)
    assert not result.ok
    assert "newline" in (result.error or "")
