from collections.abc import Sequence

class PageObject:
    def extract_text(self) -> str: ...

class PdfReader:
    pages: Sequence[PageObject]
    def __init__(self, stream: str) -> None: ...
