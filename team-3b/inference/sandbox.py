"""Per-request worker that runs one ONNX model on one input.

``EnclaveSim.run_isolated`` starts this module in a new child process for each
inference, so one request never shares memory with another. The parent writes the
model and the encoded input to stdin as two length-prefixed frames and reads the
encoded output from stdout. A failure exits with status 2 and the reason on stderr.
"""

import struct
import sys
from typing import BinaryIO

from common.tensors import decode_tensor, encode_tensor

_LENGTH = struct.Struct(">Q")


def frame(payload: bytes) -> bytes:
    """Prefix ``payload`` with its length so the reader knows where it ends."""
    return _LENGTH.pack(len(payload)) + payload


def read_frame(stream: BinaryIO) -> bytes:
    """Read one length-prefixed payload from ``stream``."""
    (length,) = _LENGTH.unpack(_read_exactly(stream, _LENGTH.size))
    return _read_exactly(stream, length)


def _read_exactly(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError(f"expected {size} bytes, input ended after {size - remaining}")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def run_model(model: bytes, input_data: bytes) -> bytes:
    """Run ``model`` on the ``.npy``-encoded input and return the first output as ``.npy``."""
    # Imported here so the parent process never loads the runtime.
    import onnxruntime

    session = onnxruntime.InferenceSession(model, providers=["CPUExecutionProvider"])
    outputs = session.run(None, {session.get_inputs()[0].name: decode_tensor(input_data)})
    return encode_tensor(outputs[0])


def main() -> int:
    """Serve one request: model and input from stdin, encoded output to stdout."""
    try:
        model = read_frame(sys.stdin.buffer)
        input_data = read_frame(sys.stdin.buffer)
        output = run_model(model, input_data)
    except Exception as error:
        sys.stderr.write(f"{type(error).__name__}: {error}\n")
        return 2
    sys.stdout.buffer.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
