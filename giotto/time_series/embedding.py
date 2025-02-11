"""Time series embedding."""
# License: Apache 2.0

import numpy as np
from sklearn.base import BaseEstimator
from ..base import TransformerResamplerMixin
from sklearn.metrics import mutual_info_score
from sklearn.neighbors import NearestNeighbors
from joblib import Parallel, delayed
from sklearn.utils.validation import check_is_fitted, check_array, column_or_1d
from ..utils.validation import validate_params


class SlidingWindow(BaseEstimator, TransformerResamplerMixin):
    """Sliding windows onto the data.

    Useful in time series analysis to convert a sequence of objects (scalar
    or array-like) into a sequence of windows on the original sequence. Each
    window stacks together consecutive objects, and consecutive windows are
    separated by a constant stride.

    Parameters
    ----------
    width : int, optional, default: ``10``
        Width of each sliding window. Each window contains ``width + 1``
        objects from the original time series.

    stride : int, optional, default: ``1``
        Stride between consecutive windows.

    Examples
    --------
    >>> import numpy as np
    >>> from giotto.time_series import SlidingWindow
    >>> # Create a time series of two-dimensional vectors, and a corresponding
    >>> # time series of scalars
    >>> X = np.arange(20).reshape(-1, 2)
    >>> y = np.arange(10)
    >>> windows = SlidingWindow(width=2, stride=3)
    >>> # Fit and transform X
    >>> X_windows = windows.fit_transform(X)
    >>> print(X_windows)
    [[[ 2  3]
      [ 4  5]
      [ 6  7]]
     [[ 8  9]
      [10 11]
      [12 13]]
     [[14 15]
      [16 17]
      [18 19]]]
    >>> # Resample y
    >>> yr = windows.resample(y)
    >>> print(yr)
    [3 6 9]

    See also
    --------
    TakensEmbedding

    Notes
    -----
    The current implementation favours the last entry over the first one,
    in the sense that the last entry of the last window always equals the last
    entry in the original time series. Hence, a number of initial entries
    (depending on the remainder of the division between :math:`n_\\mathrm{
    samples} - \\mathrm{width} - 1` and the stride) may be lost.

    """
    _hyperparameters = {'width': [int, (1, np.inf)],
                        'stride': [int, (1, np.inf)]}

    def __init__(self, width=10, stride=1):
        self.width = width
        self.stride = stride

    def _slice_windows(self, X):
        n_samples = X.shape[0]
        n_windows = (n_samples - self.width - 1) // self.stride + 1

        window_slices = [(n_samples - i * self.stride - self.width - 1,
                          n_samples - i * self.stride)
                         for i in reversed(range(n_windows))]

        return window_slices

    def fit(self, X, y=None):
        """Do nothing and return the estimator unchanged.

        This method is there to implement the usual scikit-learn API and hence
        work in pipelines.

        Parameters
        ----------
        X : ndarray, shape (n_samples, ...)
            Input data.

        y : None
            Ignored.

        Returns
        -------
        self

        """
        validate_params(self.get_params(), self._hyperparameters)
        check_array(X, ensure_2d=False, allow_nd=True)

        self._is_fitted = True
        return self

    def transform(self, X, y=None):
        """Slide windows over X.

        Parameters
        ----------
        X : ndarray, shape (n_samples, ...)
            Input data.

        y : None
            Ignored.

        Returns
        -------
        Xt : ndarray, shape (n_windows, n_samples_window, ...)
            Windows of consecutive entries of the original time series.
            ``n_windows = (n_samples - width - 1) // stride  + 1``, and
            ``n_samples_window = width + 1``.

        """
        # Check if fit had been called
        check_is_fitted(self, ['_is_fitted'])
        X = check_array(X, ensure_2d=False, allow_nd=True)

        window_slices = self._slice_windows(X)

        Xt = np.stack([X[begin:end] for begin, end in window_slices])
        return Xt

    def resample(self, y, X=None):
        """Resample `y` so that, for any i > 0, the minus i-th entry of the
        resampled vector corresponds in time to the last entry of the minus
        i-th window produced by :meth:`transform`.

        Parameters
        ----------
        y : ndarray, shape (n_samples,)
            Target.

        X : None
            There is no need for input data, yet the pipeline API requires
            this parameter.

        Returns
        -------
        yr : ndarray, shape (n_samples_new,)
            The resampled target. ``n_samples_new = (n_samples - time_delay *
            (dimension - 1) - 1) // stride + 1``.

        """
        # Check if fit had been called
        check_is_fitted(self, ['_is_fitted'])
        yr = column_or_1d(y)

        yr = np.flip(yr)
        yr = np.flip(yr[:-self.width:self.stride])
        return yr


class TakensEmbedding(BaseEstimator, TransformerResamplerMixin):
    """Representation of a univariate time series as a time series of
    point clouds.

    Based on a time-delay embedding technique named after F. Takens [1]_.
    Given a discrete time series :math:`(X_0, X_1, \\ldots)` and a sequence
    of evenly sampled times :math:`t_0, t_1, \\ldots`, one extracts a set
    of :math:`d`-dimensional vectors of the form :math:`(X_{t_i}, X_{t_i +
    \\tau}, \\ldots , X_{t_i + (d-1)\\tau})` for :math:`i = 0, 1, \\ldots`.
    This set is called the `Takens embedding <https://www.giotto.ai/theory>`_
    of the time series and can be interpreted as a point cloud.

    The difference between :math:`t_{i+1}` and :math:`t_i` is called the
    stride, :math:`\\tau` is called the time delay, and :math:`d` is called
    the (embedding) dimension.

    If :math:`d` and :math:`\\tau` are not explicitly set, suitable values
    are searched for during :meth:`fit`. [2]_ [3]_

    Parameters
    ----------
    parameters_type : ``'search'`` | ``'fixed'``, optional, default: \
                      ``'search'``
        If set to ``'fixed'``, the values of `time_delay` and `dimension`
        are used directly in :meth:`transform`. If set to ``'search'``,
        those values are only used as upper bounds in a search as follows:
        first, an optimal time delay is found by minimising the time delayed
        mutual information; then, a heuristic based on an algorithm in [2]_ is
        used to select an embedding dimension which, when increased, does not
        reveal a large proportion of "false nearest neighbors".

    time_delay : int, optional, default: ``1``
        Time delay between two consecutive values for constructing one
        embedded point. If `parameters_type` is ``'search'``,
        it corresponds to the maximal embedding time delay that will be
        considered.

    dimension : int, optional, default: ``5``
        Dimension of the embedding space. If `parameters_type` is ``'search'``,
        it corresponds to the maximum embedding dimension that will be
        considered.

    stride : int, optional, default: ``1``
        Stride duration between two consecutive embedded points. It defaults
        to 1 as this is the usual value in the statement of Takens's embedding
        theorem.

    n_jobs : int or None, optional, default: ``None``
        The number of jobs to use for the computation. ``None`` means 1 unless
        in a :obj:`joblib.parallel_backend` context. ``-1`` means using all
        processors.

    Attributes
    ----------
    time_delay_ : int
        Actual embedding time delay used to embed. If
        `parameters_type` is ``'search'``, it is the calculated optimal
        embedding time delay and is less than or equal to `time_delay`.
        Otherwise it is equal tp `time_delay`.

    dimension_ : int
        Actual embedding dimension used to embed. If `parameters_type` is
        ``'search'``, it is the calculated optimal embedding dimension and
        is less than or equal to `dimension`. Otherwise it is equal to
        `dimension`.

    Examples
    --------
    >>> import numpy as np
    >>> from giotto.time_series import TakensEmbedding
    >>> # Create a noisy signal
    >>> n_samples = 10000
    >>> signal_noise = np.asarray([np.sin(x / 50) + 0.5 * np.random.random()
    ...     for x in range(n_samples)])
    >>> # Set up the transformer
    >>> embedder = TakensEmbedding(parameters_type='search', dimension=5,
    ...                            time_delay=5, n_jobs=-1)
    >>> # Fit and transform
    >>> embedded_noise = embedder.fit_transform(signal_noise)
    >>> print('Optimal embedding time delay based on mutual information:',
    ...       embedder.time_delay_)
    Optimal embedding time delay based on mutual information: 5
    >>> print('Optimal embedding dimension based on false nearest neighbors:',
    ...       embedder.dimension_)
    Optimal embedding dimension based on false nearest neighbors: 2
    >>> print(embedded_noise.shape)
    (9995, 2)

    See also
    --------
    SlidingWindow, giotto.homology.VietorisRipsPersistence

    Notes
    -----
    The current implementation favours the last value over the first one,
    in the sense that the last coordinate of the last vector in a Takens
    embedded time series always equals the last value in the original time
    series. Hence, a number of initial values (depending on the remainder of
    the division between :math:`n_\\mathrm{samples} - d(\\tau - 1) - 1` and
    the stride) may be lost.

    References
    ----------
    .. [1] F. Takens, "Detecting strange attractors in turbulence". In: Rand
           D., Young LS. (eds) *Dynamical Systems and Turbulence, Warwick
           1980*. Lecture Notes in Mathematics, vol. 898. Springer, 1981;
           doi: `10.1007/BFb0091924 <https://doi.org/10.1007/BFb0091924>`_.

    .. [2] M. B. Kennel, R. Brown, and H. D. I. Abarbanel, "Determining
           embedding dimension for phase-space reconstruction using a
           geometrical construction"; *Phys. Rev. A* **45**, pp. 3403--3411,
           1992; doi: `10.1103/PhysRevA.45.3403
           <https://doi.org/10.1103/PhysRevA.45.3403>`_.

    .. [3] N. Sanderson, "Topological Data Analysis of Time Series using
           Witness Complexes"; PhD thesis, University of Colorado at
           Boulder, 2018; `https://scholar.colorado.edu/math_gradetds/67
           <https://scholar.colorado.edu/math_gradetds/67>`_.

    """
    _hyperparameters = {'parameters_type': [str, ['fixed', 'search']],
                        'time_delay': [int, (1, np.inf)],
                        'dimension': [int, (1, np.inf)],
                        'stride': [int, (1, np.inf)]}

    def __init__(self, parameters_type='search', time_delay=1, dimension=5,
                 stride=1, n_jobs=None):
        self.parameters_type = parameters_type
        self.time_delay = time_delay
        self.dimension = dimension
        self.stride = stride
        self.n_jobs = n_jobs

    @staticmethod
    def _embed(X, time_delay, dimension, stride):
        n_points = (X.shape[0] - time_delay * (dimension - 1) - 1)\
                   // stride + 1

        X = np.flip(X)
        points_ = [X[j * stride:j * stride + time_delay * dimension:time_delay]
                   .flatten() for j in range(n_points)]
        X_embedded = np.stack(points_)

        return np.flip(X_embedded).reshape(n_points, dimension)

    @staticmethod
    def _mutual_information(X, time_delay, n_bins):
        """Calculate the mutual information given the delay."""
        contingency = np.histogram2d(X.reshape((-1,))[:-time_delay],
                                     X.reshape((-1,))[time_delay:],
                                     bins=n_bins)[0]
        mutual_information = mutual_info_score(None, None,
                                               contingency=contingency)
        return mutual_information

    @staticmethod
    def _false_nearest_neighbors(X, time_delay, dimension,
                                 stride=1):
        """Calculate the number of false nearest neighbours of embedding
        dimension. """
        X_embedded = TakensEmbedding._embed(X, time_delay, dimension, stride)

        neighbor = NearestNeighbors(n_neighbors=2, algorithm='auto').fit(
            X_embedded)
        distances, indices = neighbor.kneighbors(X_embedded)
        distance = distances[:, 1]
        XNeighbor = X[indices[:, 1]]

        epsilon = 2.0 * np.std(X)
        tolerance = 10

        dim_by_delay = -dimension * time_delay
        non_zero_distance = distance[:dim_by_delay] > 0

        false_neighbor_criteria = \
            np.abs(np.roll(X, dim_by_delay)[
                   X.shape[0] - X_embedded.shape[0]:dim_by_delay] -
                   np.roll(XNeighbor, dim_by_delay)[:dim_by_delay]) \
            / distance[:dim_by_delay] > tolerance

        limited_dataset_criteria = distance[:dim_by_delay] < epsilon

        n_false_neighbors = np.sum(
            non_zero_distance * false_neighbor_criteria *
            limited_dataset_criteria)
        return n_false_neighbors

    def fit(self, X, y=None):
        """If necessary, compute the optimal time delay and embedding
        dimension. Then, return the estimator.

        This method is there to implement the usual scikit-learn API and hence
        work in pipelines.

        Parameters
        ----------
        X : ndarray, shape (n_samples,) or (n_samples, 1)
            Input data.

        y : None
            There is no need for a target in a transformer, yet the pipeline
            API requires this parameter.

        Returns
        -------
        self : object

        """
        validate_params(self.get_params(), self._hyperparameters)
        X = check_array(X, ensure_2d=False)
        if X.ndim == 1:
            X = X[:, None]

        if self.parameters_type == 'search':
            mutual_information_list = Parallel(n_jobs=self.n_jobs)(
                delayed(self._mutual_information)(X, time_delay, n_bins=100)
                for time_delay in range(1, self.time_delay + 1))
            self.time_delay_ = mutual_information_list.index(
                min(mutual_information_list)) + 1

            n_false_nbhrs_list = Parallel(n_jobs=self.n_jobs)(
                delayed(self._false_nearest_neighbors)(
                    X, self.time_delay_, dim, stride=1)
                for dim in range(1, self.dimension + 3))
            variation_list = [np.abs(n_false_nbhrs_list[dim - 1]
                                     - 2 * n_false_nbhrs_list[dim] +
                                     n_false_nbhrs_list[dim + 1])
                              / (n_false_nbhrs_list[dim] + 1) / dim
                              for dim in range(2, self.dimension + 1)]
            self.dimension_ = variation_list.index(min(variation_list)) + 2

        else:
            self.time_delay_ = self.time_delay
            self.dimension_ = self.dimension

        return self

    def transform(self, X, y=None):
        """Compute the Takens embedding of `X`.

        Parameters
        ----------
        X : ndarray, shape (n_samples,) or (n_samples, 1)
            Input data.

        y : None
            Ignored.

        Returns
        -------
        Xt : ndarray, shape (n_points, n_dimension)
            Output point cloud in Euclidean space of dimension given by
            :attr:`dimension_`. ``n_points = (n_samples - time_delay *
            (dimension - 1) - 1) // stride + 1``.

        """
        # Check if fit had been called
        check_is_fitted(self, ['time_delay_', 'dimension_'])
        Xt = check_array(X, ensure_2d=False)
        if Xt.ndim == 1:
            Xt = Xt[:, None]
        Xt = self._embed(Xt, self.time_delay_, self.dimension_, self.stride)

        return Xt

    def resample(self, y, X=None):
        """Resample `y` so that, for any i > 0, the minus i-th entry of the
        resampled vector corresponds in time to the last coordinate of the
        minus i-th embedding vector produced by :meth:`transform`.

        Parameters
        ----------
        y : ndarray, shape (n_samples,)
            Target.

        X : None
            There is no need for input data, yet the pipeline API requires
            this parameter.

        Returns
        -------
        yr : ndarray, shape (n_samples_new,)
            The resampled target. ``n_samples_new = (n_samples - time_delay *
            (dimension - 1) - 1) // stride + 1``.

        """
        # Check if fit had been called
        check_is_fitted(self, ['time_delay_', 'dimension_'])
        yr = column_or_1d(y)

        yr = np.flip(yr)
        final_index = -self.time_delay_ * (self.dimension_ - 1)
        yr = np.flip(yr[:final_index:self.stride])
        return yr
