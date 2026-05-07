from typing import Any

import numpy as np
from numpy.typing import NDArray

def sobel(
    input: NDArray[Any],  # noqa: A002 - scipy uses 'input' as the kwarg name
    axis: int = -1,
    output: NDArray[Any] | None = None,
    mode: str = "reflect",
    cval: float = 0.0,
) -> NDArray[np.float32]: ...
def gaussian_filter(
    input: NDArray[Any],  # noqa: A002
    sigma: float | tuple[float, ...],
    order: int | tuple[int, ...] = 0,
    output: NDArray[Any] | None = None,
    mode: str = "reflect",
    cval: float = 0.0,
    truncate: float = 4.0,
) -> NDArray[np.float32]: ...
def distance_transform_edt(
    input: NDArray[Any],  # noqa: A002 - scipy uses 'input' as the kwarg name
    sampling: float | tuple[float, ...] | None = None,
    return_distances: bool = True,
    return_indices: bool = False,
    distances: NDArray[Any] | None = None,
    indices: NDArray[Any] | None = None,
) -> NDArray[np.float64]: ...
