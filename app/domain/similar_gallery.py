from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class LatestSimilarSearch:
    source_gid: int
    selected_text: str
    result_gids: Tuple[int, ...]
    searched_at: int = 0
