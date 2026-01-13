from .config import PUBLIC_URL
def pay_url(tg_id: int) -> str:
    return f"{PUBLIC_URL.rstrip('/')}/pay?tg_id={tg_id}"
