# Synthetic scene for Lantern. This is not from a commercial game.

define m = Character(_("美咲"), color="#c8a2c8")
define k = Character(_("健一"), color="#88aacc")

label start:
    m "こんにちは、[player_name]。{w}今日はいい天気ね。"
    k "美咲、学園の門で待ってたよ。"
    m happy "ごめん、ちょっと遅れちゃった。"
    voice "audio/misaki_01.ogg"
    m "じゃあ、行こっか。"

    menu:
        "どこへ行く？"

        "屋上へ行く":
            jump roof

        "図書室に残る":
            k "分かった。また後で。"

label roof:
    m "ここ、静かだね。%(place)sみたい。"
    "風が強い。"
    m "気をつけて{b}ね{/b}。" id roof_careful
