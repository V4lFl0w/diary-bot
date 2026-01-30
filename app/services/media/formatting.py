from __future__ import annotations

# app/services/assistant.py






MEDIA_NOT_FOUND_REPLY_RU = (
    "Не могу уверенно найти по этому запросу.\n"
    "Дай 1–2 факта: актёр/актриса, примерный год, страна или что происходит в сцене."
)

def build_media_context(items: list[dict]) -> str:
    """Numbered list for TMDb search results."""
    if not items:
        return MEDIA_NOT_FOUND_REPLY_RU
    lines: list[str] = ["Нашёл варианты:"]
    for i, it in enumerate(items[:10], 1):
        try:
            lines.append(f"\n{i}) {_format_one_media(it)}")
        except Exception:
            title = it.get("title") or it.get("name") or "Без названия"
            year = it.get("year") or ""
            lines.append(f"\n{i}) {title} {f'({year})' if year else ''}".strip())
    return "\n".join(lines)

def _format_media_pick(item: dict) -> str:
    """
    Small, safe formatter for a picked TMDb item.
    item keys may vary (movie/tv). We keep it short.
    """
    title = item.get("title") or item.get("name") or "Без названия"
    year = ""
    d = item.get("release_date") or item.get("first_air_date") or ""
    if isinstance(d, str) and len(d) >= 4:
        year = d[:4]
    overview = (item.get("overview") or "").strip()
    if overview and len(overview) > 500:
        overview = overview[:500].rsplit(" ", 1)[0] + "…"
    media_type = item.get("media_type") or ("tv" if item.get("name") else "movie")
    tmdb_id = item.get("id")
    url = ""
    if tmdb_id:
        url = f"https://www.themoviedb.org/{media_type}/{tmdb_id}"
    lines = [f"🎬 {title}" + (f" ({year})" if year else "")]
    if overview:
        lines.append("")
        lines.append(overview)
    if url:
        lines.append("")
        lines.append(url)
    return "\n".join(lines)

def _title_tokens(x: str) -> set[str]:
    x = (x or "").lower()
    x = x.replace("ё", "е")
    out = []
    w = []
    for ch in x:
        if ch.isalnum() or ch in ("-", " "):
            w.append(ch)
        else:
            w.append(" ")
    x = "".join(w)
    x = " ".join(x.split())
    for t in x.split():
        if len(t) > 1:
            out.append(t)
    return set(out)

def _tmdb_score_item(query: str, it: dict, *, year_hint: str | None = None, lang_hint: str | None = None) -> tuple[float, str]:
    """Return (score 0..1, why_short)."""
    q = (query or "").strip()
    title = (it.get("title") or it.get("name") or "").strip()
    orig_lang = (it.get("original_language") or "").strip().lower()
    year = str(it.get("year") or "")[:4]

    ql = q.lower()
    tl = title.lower()

    score = 0.0
    why = []

    # title match
    if title and q:
        if tl == ql:
            score += 0.55
            why.append("точное совпадение названия")
        elif ql and (ql in tl or tl in ql):
            score += 0.40
            why.append("совпали ключевые слова в названии")
        else:
            qt = _title_tokens(q)
            tt = _title_tokens(title)
            if qt and tt:
                inter = len(qt & tt)
                uni = len(qt | tt)
                j = inter / max(1, uni)
                score += 0.35 * min(1.0, j * 1.8)
                if inter:
                    why.append("частичное совпадение слов")

    # year match
    if year_hint and year and year_hint == year:
        score += 0.18
        why.append("совпадает год")

    # stabilizers
    pop = float(it.get("popularity") or 0.0)
    vc = float(it.get("vote_count") or 0.0)
    score += min(0.12, (pop / 200.0) * 0.12)
    score += min(0.10, (vc / 5000.0) * 0.10)

    # language hint
    if lang_hint:
        lh = (lang_hint or "").lower().strip()
        if lh and orig_lang and lh == orig_lang:
            score += 0.05

    score = max(0.0, min(1.0, score))
    return score, (", ".join(why[:2]) if why else "похоже по общим признакам")

def _format_media_ranked(query: str, items: list[dict], *, year_hint: str | None = None, lang: str = "ru", source: str = "tmdb") -> str:
    """Best match + why + 2–3 alternatives. Buttons-first. Digits only as fallback."""
    if not items:
        return MEDIA_NOT_FOUND_REPLY_RU

    def _short_overview(it: dict, lim: int = 220) -> str:
        ov = (it.get("overview") or "").strip()
        if not ov:
            return ""
        if len(ov) <= lim:
            return ov
        cut = ov[:lim].rsplit(" ", 1)[0].strip()
        return (cut + "…") if cut else (ov[:lim] + "…")

    # score + reason
    scored: list[tuple[float, str, dict]] = []
    for it in items:
        try:
            sc, why = _tmdb_score_item(query, it, year_hint=year_hint, lang_hint=("ru" if lang == "ru" else None))
        except Exception:
            sc, why = 0.0, "похоже по общим признакам"
        scored.append((float(sc), str(why), it))
    scored.sort(key=lambda x: x[0], reverse=True)

    best_sc, best_why, best = scored[0]
    alts = scored[1:4]

    # fields
    t = (best.get("title") or best.get("name") or "—")
    y = (best.get("year") or "—")
    r = (best.get("vote_average") or "—")
    kind = (best.get("media_type") or "").strip()
    kind_ru = "сериал" if kind == "tv" else "фильм" if kind == "movie" else (kind or "медиа")

    ov = _short_overview(best)

    TH = 0.58
    if best_sc < TH:
        out: list[str] = []
        out.append("🎬 Нашёл варианты, но уверенность низкая.")
        out.append("")
        for i, (sc, why, it) in enumerate(scored[:3], start=1):
            tt = (it.get("title") or it.get("name") or "—")
            yy = (it.get("year") or "—")
            rr = (it.get("vote_average") or "—")
            kk = (it.get("media_type") or "").strip()
            kk_ru = "сериал" if kk == "tv" else "фильм" if kk == "movie" else (kk or "медиа")
            out.append(f"{i}) {tt} ({yy}) — {kk_ru} · ⭐ {rr} · {why}")
        out.append("")
        out.append("🧩 Уточни 1 деталь: год / актёр / страна / серия-эпизод / что происходит в сцене.")
        out.append("👉 Нажми кнопку: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить.")
        out.append("Если кнопок нет — можешь ответить цифрой 1–3.")
        return "\n".join(out)

    out2: list[str] = []
    out2.append(f"✅ Лучшее совпадение: {t} ({y}) — {kind_ru} · ⭐ {r}")
    out2.append(f"Почему: {best_why}.")
    if ov:
        out2.append("")
        out2.append(ov)

    if alts:
        out2.append("")
        out2.append("Альтернативы (если не то):")
        for i, (sc, why, it) in enumerate(alts, start=1):
            tt = (it.get("title") or it.get("name") or "—")
            yy = (it.get("year") or "—")
            rr = (it.get("vote_average") or "—")
            kk = (it.get("media_type") or "").strip()
            kk_ru = "сериал" if kk == "tv" else "фильм" if kk == "movie" else (kk or "медиа")
            out2.append(f"{i}) {tt} ({yy}) — {kk_ru} · ⭐ {rr}")

    out2.append("")
    out2.append("👉 Нажми кнопку: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить.")
    out2.append("Если кнопок нет — можешь ответить цифрой 1–3.")
    return "\n".join(out2)
def _format_one_media(item: dict) -> str:
    # items come from tmdb_search_multi(): title/year/media_type/overview/vote_average
    title = (item.get("title") or item.get("name") or "Без названия").strip()
    year = (item.get("year") or "").strip()
    overview = (item.get("overview") or "").strip()
    rating = item.get("vote_average", None)
    kind = (item.get("media_type") or "").strip()
    kind_ru = (
        "сериал" if kind == "tv" else "фильм" if kind == "movie" else kind or "медиа"
    )

    line = f"🎬 {title}"
    if year:
        line += f" ({year})"
    line += f" — {kind_ru}"

    if rating is not None:
        try:
            line += f" • ⭐ {float(rating):.1f}"
        except Exception:
            pass

    if overview:
        line += f"\n\n{overview[:700]}"
    return line
