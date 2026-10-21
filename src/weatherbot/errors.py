class WeatherbotError(Exception):
    def __init__(self, source: str, detail: str) -> None:
        self.source = source
        self.detail = detail
        super().__init__(f"{source}: {detail}")
