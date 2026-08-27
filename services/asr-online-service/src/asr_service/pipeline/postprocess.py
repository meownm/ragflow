def finalize_transcript(raw: dict) -> dict:
    return {"transcript": raw.get("transcript", ""), "segments": raw.get("segments", [])}
