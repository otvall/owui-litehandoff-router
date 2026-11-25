"""Frame provider chunks without decoding incomplete UTF-8 characters."""


class StreamLines:
    def __init__(self):
        self.pending = b""
        self.received = 0

    def feed(self, chunk):
        data = chunk.encode() if isinstance(chunk, str) else chunk
        self.received += len(data)
        lines = (self.pending + data).split(b"\n")
        self.pending = lines.pop()
        return lines

    def finish(self):
        tail, self.pending = self.pending, b""
        return (tail,) if tail else ()
