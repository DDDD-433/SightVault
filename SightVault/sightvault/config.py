from dataclasses import dataclass, field
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent

@dataclass
class Config:
    root: Path = ROOT
    data: Path = Path(os.environ.get("SIGHTVAULT_DATA", str(ROOT / "data")))
    models: Path = Path(os.environ.get("SIGHTVAULT_MODELS", str(ROOT / ".models")))
    device: str = os.environ.get("SIGHTVAULT_DEVICE", "auto")
    ocr: str = os.environ.get("SIGHTVAULT_OCR", "auto")
    vision: str = field(default_factory=lambda: os.environ.get("SIGHTVAULT_VISION", "minicpm"))
    max_pixels: int = 40_000_000
    max_file_bytes: int = 30 * 1024 * 1024
    max_files: int = 2000

    def __post_init__(self):
        self.vision = self.vision.strip().lower()
        if self.vision not in ("minicpm", "lfm3"):
            raise ValueError("SIGHTVAULT_VISION must be 'minicpm' or 'lfm3'.")

    def prepare(self):
        for directory in [self.data, self.data / "images", self.data / "thumbs"]:
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self):
        return self.data / "sightvault.sqlite3"

