from typing import Optional


def process(data: list[int], flag: bool = True) -> Optional[str]:
    if flag:
        return str(sum(data))
    return None


count: int = 0
name: str = "hello"
