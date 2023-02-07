"""
packing.py
------------

Pack rectangular regions onto larger rectangular regions.
"""
import time
import numpy as np

from ..util import allclose
from ..constants import log

# floating point zero
_TOL_ZERO = 1e-12


class RectangleBin:
    """
    An N-dimensional binary space partition tree for packing
    hyper-rectangles. Split logic is pure `numpy` but behaves
    similarly to `scipy.spatial.Rectangle`.

    Mostly useful for packing 2D textures and 3D boxes and
    has not been tested outside of 2 and 3 dimensions.

    Original article about using this for packing textures:
    http://www.blackpawn.com/texts/lightmaps/
    """

    def __init__(self, bounds):
        """
        Create a rectangular bin.

        Parameters
        ------------
        bounds : (2, dimension *) float
          Bounds array are `[mins, maxes]`
        """
        # this is a *binary* tree so regardless of the dimensionality
        # of the rectangles each node has exactly two children
        self.child = []
        # is this node occupied.
        self.occupied = False
        # assume bounds are a list
        self.bounds = np.array(bounds, dtype=np.float64)

    @property
    def extents(self):
        """
        Bounding box size.

        Returns
        ----------
        extents : (dimension,) float
          Edge lengths of bounding box
        """
        bounds = self.bounds
        return bounds[1] - bounds[0]

    def insert(self, size, rotate=True):
        """
        Insert a rectangle into the bin.

        Parameters
        -------------
        size : (dimension,) float
          Size of rectangle to insert/

        Returns
        ----------
        inserted : (2,) float or None
          Position of insertion in the tree or None
          if the insertion was unsucessful.
        """

        for child in self.child:
            # try inserting into child cells
            attempt = child.insert(size=size, rotate=rotate)
            if attempt is not None:
                return attempt

        # can't insert into occupied cells
        if self.occupied:
            return None

        # shortcut for our bounds
        bounds = self.bounds
        extents = bounds[1] - bounds[0]

        if rotate:
            # we are allowed to rotate the rectangle
            fits = False
            for roll in range(len(size)):
                size_test = extents - _roll(size, roll)
                fits = (size_test > -_TOL_ZERO).all()
                if fits or not rotate:
                    size = _roll(size, roll)
                    break
            if not fits:
                return None
        else:
            # compare the bin size to the insertion candidate size
            # manually compute extents here to avoid function call
            size_test = extents - size
            roll = 0
            if (size_test < -_TOL_ZERO).any():
                return None

        # since the cell is big enough for the current rectangle, either it
        # is going to be inserted here, or the cell is going to be split
        # either way the cell is now occupied.
        self.occupied = True

        # this means the inserted rectangle fits perfectly
        # since we already checked to see if it was negative
        # no abs is needed
        if (size_test < _TOL_ZERO).all():
            return (bounds, roll)

        # pick the axis to split along
        axis = size_test.argmax()
        # split hyper-rectangle along axis
        # note that split is *absolute* distance not offset
        # so we have to add the current min to the size
        splits = np.tile(bounds, (2, 1))
        splits[1:3, axis] = bounds[0][axis] + size[axis]

        # assign two children
        self.child[:] = RectangleBin(splits[:2]), RectangleBin(splits[2:])

        # insert the requested item into the first child
        return self.child[0].insert(size, rotate=rotate)


def _roll(a, count):
    """
    A speedup for `numpy.roll` that only works
    on flat arrays and is fast on 2D and 3D and
    reverts to `numpy.roll` for other cases.

    Parameters
    -----------
    a : (n,) any
      Array to roll
    count : int
      Number of places to shift array

    Returns
    ---------
    rolled : (n,) any
      Input array shifted by requested amount

    """
    # a lookup table for roll in 2 and 3 dimensions
    lookup = [[[0, 1], [1, 0]], [[0, 1, 2], [2, 0, 1], [1, 2, 0]]]
    try:
        # roll the array using advanced indexing and a lookup table
        return a[lookup[len(a) - 2][count]]
    except IndexError:
        # failing that return the results using concat
        return np.concatenate([a[-count:], a[:-count]])


def rectangles_single(rect, size=None, shuffle=False, rotate=True):
    """
    Execute a single insertion order of smaller rectangles onto
    a larger rectangle using a binary space partition tree.

    Parameters
    ----------
    rectangles : (n, 2) float
      An array of (width, height) pairs
      representing the rectangles to be packed.
    size : (2,) float
      Width, height of rectangular sheet
    shuffle : bool
      Whether or not to shuffle the insert order of the
      smaller rectangles, as the final packing density depends
      on insertion order.

    Returns
    ---------
    density : float
      Area filled over total sheet area
    offset :  (m,2) float
      Offsets to move rectangles to their packed location
    inserted : (n,) bool
      Which of the original rectangles were packed
    consumed_box : (2,) float
      Bounding box size of packed result
    """

    rect = np.asanyarray(rect, dtype=np.float64)
    dim = rect.shape[1]

    offset = np.zeros((len(rect), 2, dim))
    consume = np.zeros(len(rect), dtype=bool)
    rolled = np.zeros(len(rect), dtype=int)

    # start by ordering them by maximum length
    order = np.argsort(rect.max(axis=1))[::-1]

    if shuffle:
        # maximum index to shuffle
        max_idx = int(np.random.random() * len(rect)) - 1
        # reorder with permutations
        order[:max_idx] = np.random.permutation(order[:max_idx])

    if size is None:
        # if no bounds are passed start it with the maximum size
        # along an axis which will almost certainly require re-rooting
        root_bounds = [[0.0] * dim, rect[rect.ptp(axis=1).argmax()]]
    else:
        # restrict the bounds to passed size and disallow re-rooting
        root_bounds = [[0.0] * dim, size]

    # the current root node to insert each rectangle
    root = RectangleBin(bounds=root_bounds)

    for index in order:
        # the current rectangle to be inserted
        rectangle = rect[index]
        # try to insert the hyper-rectangle into children
        inserted = root.insert(rectangle, rotate=rotate)

        if inserted is None and size is None:
            # we failed to insert into children
            # so we need to create a new parent
            # get the size of the current root node
            bounds = root.bounds
            extents = bounds.ptp(axis=0)

            best = np.inf
            for roll in range(len(extents)):

                stack = np.array([extents, _roll(rectangle, roll)])

                # we are going to combine two hyper-rect
                # so we have `dim` choices on ways to split
                # choose the split that minimizes the new hyper-volume
                # the new AABB is going to be the `max` of the lengths
                # on every dim except one which will be the `sum`
                dim = len(extents)
                ch = np.tile(stack.max(axis=0), (len(extents), 1))
                np.fill_diagonal(ch, stack.sum(axis=0))

                # choose the new AABB by which one minimizes hyper-volume
                choice_prod = np.product(ch, axis=1)
                if choice_prod.min() < best:
                    choices = ch
                    choices_idx = choice_prod.argmin()
                    choices_roll = roll
                    best = choice_prod[choices_idx]
                if not rotate:
                    break

            # we now know the full extent of the AABB
            new_max = bounds[0] + choices[choices_idx]

            # offset the new bounding box corner
            new_min = bounds[0].copy()
            new_min[choices_idx] += extents[choices_idx]

            # original bounds may be stretched
            new_ori_max = np.vstack((bounds[1], new_max)).max(axis=0)
            new_ori_max[choices_idx] = bounds[1][choices_idx]

            assert (new_ori_max >= bounds[1]).all()

            # the bounds containing the original sheet
            bounds_ori = np.array([bounds[0], new_ori_max])
            # the bounds containing the location to insert
            # the new rectangle
            bounds_ins = np.array([new_min, new_max])

            # generate the new root node
            new_root = RectangleBin([bounds[0], new_max])
            # this node has children so it is occupied
            new_root.occupied = True
            # create a bin for both bounds
            new_root.child = [RectangleBin(bounds_ori),
                              RectangleBin(bounds_ins)]

            # insert the original sheet into the new tree
            root_offset = new_root.child[0].insert(
                bounds.ptp(axis=0), rotate=rotate)
            # we sized the cells so original tree would fit
            assert root_offset is not None

            # existing inserts need to be moved
            if not allclose(root_offset[0][0], 0.0):
                offset[consume] += root_offset[0][0]

            # insert the child that didn't fit before into the other child
            child = new_root.child[1].insert(rectangle, rotate=rotate)
            # since we re-sized the cells to fit insertion should always work
            assert child is not None

            offset[index] = child[0]
            rolled[index] = child[1]
            consume[index] = True

            # subsume the existing tree into a new root
            root = new_root

        elif inserted is not None:
            # we sucessfully inserted
            offset[index] = inserted[0]
            rolled[index] = inserted[1]

            consume[index] = True

    return offset, consume


def paths(paths, **kwargs):
    """
    Pack a list of Path2D objects into a rectangle.

    Parameters
    ------------
    paths: (n,) Path2D
      Geometry to be packed

    Returns
    ------------
    packed : trimesh.path.Path2D
      Object containing input geometry
    inserted : (m,) int
      Indexes of paths inserted into result
    """
    from .util import concatenate

    # default quantity to 1
    quantity = [i.metadata.get('quantity', 1)
                for i in paths]

    # pack using exterior polygon (will OBB)
    packable = [i.polygons_closed[i.root[0]] for i in paths]

    # pack the polygons using rectangular bin packing
    inserted, transforms = polygons(polygons=packable,
                                    quantity=quantity,
                                    **kwargs)

    multi = []
    for i, T in zip(inserted, transforms):
        multi.append(paths[i].copy())
        multi[-1].apply_transform(T)
    # append all packed paths into a single Path object
    packed = concatenate(multi)

    return packed, inserted


def polygons(polygons,
             size=None,
             iterations=50,
             density_escape=.95,
             spacing=0.094,
             quantity=None,
             **kwargs):
    """
    Pack polygons into a rectangle by taking each Polygon's OBB
    and then packing that as a rectangle.

    Parameters
    ------------
    polygons : (n,) shapely.geometry.Polygon
      Source geometry
    size : (2,) float
      Size of rectangular sheet
    iterations : int
      Number of times to run the loop
    density_escape : float
      When to exit early (0.0 - 1.0)
    spacing : float
      How big a gap to leave between polygons
    quantity : (n,) int, or None
      Quantity of each Polygon

    Returns
    -------------
    overall_inserted : (m,) int
      Indexes of inserted polygons
    packed : (m, 3, 3) float
      Homogeonous transforms from original frame to
      packed frame.
    """

    from .polygons import polygons_obb

    if quantity is None:
        quantity = np.ones(len(polygons), dtype=np.int64)
    else:
        quantity = np.asanyarray(quantity, dtype=np.int64)
    if len(quantity) != len(polygons):
        raise ValueError('quantity must match polygons')

    # find the oriented bounding box of the polygons
    obb, rect = polygons_obb(polygons)

    # pad all sides of the rectangle
    rect += 2.0 * spacing
    # move the OBB transform so the polygon is centered
    # in the padded rectangle
    for i, r in enumerate(rect):
        obb[i][:2, 2] += r * .5

    # for polygons occurring multiple times
    indexes = np.hstack([np.ones(q, dtype=np.int64) * i
                         for i, q in enumerate(quantity)])
    # stack using advanced indexing
    obb = obb[indexes]
    rect = rect[indexes]

    # store timing
    tic = time.time()

    # run packing for a number of iterations
    bounds, inserted = rectangles(
        rect=rect,
        size=size,
        spacing=spacing,
        density_escape=density_escape,
        iterations=iterations, **kwargs)

    toc = time.time()
    log.debug('packing finished %i iterations in %f seconds',
              i + 1,
              toc - tic)
    log.debug('%i/%i parts were packed successfully',
              np.sum(inserted),
              quantity.sum())

    # transformations to packed positions
    packed = obb[inserted]

    # apply the offset and inter- polygon spacing
    packed.reshape(-1, 9)[:, [2, 5]] += bounds[:, 0, :] + spacing

    return indexes[inserted], packed


def rectangles(rect,
               size=None,
               density_escape=0.9,
               spacing=0.0,
               iterations=50,
               rotate=True,
               quanta=None):
    """
    Run multiple iterations of rectangle packing.

    Parameters
    ------------
    rect : (n, 2) float
      Size of rect to be packed
    size : None or (2,) float
      Size of sheet to pack onto
    density_escape : float
      Exit early if density is above this threshold
    spacing : float
      Distance to allow between rect
    iterations : int
      Number of iterations to run
    quanta : None or float


    Returns
    ---------
    density : float
      Area filled over total sheet area
    offset :  (m,2) float
      Offsets to move rect to their packed location
    inserted : (n,) bool
      Which of the original rect were packed
    consumed_box : (2,) float
      Bounding box size of packed result
    """
    rect = np.array(rect)

    dim = rect.shape[1]

    # hyper-volume: area in 2D, volume in 3D, party in 4D
    area = np.product(rect, axis=1)
    # best density percentage in 0.0 - 1.0
    best_density = 0.0
    # how many rect were inserted
    best_count = 0

    for i in range(iterations):
        # run a single insertion order
        # don't shuffle the first run, shuffle subsequent runs
        bounds, insert = rectangles_single(
            rect=rect, size=size, shuffle=(i != 0))

        count = insert.sum()
        extents = bounds[insert].reshape((-1, dim)).ptp(axis=0)

        if quanta is not None:
            # compute the density using an upsized quanta
            extents = np.ceil(extents / quanta) * quanta

        # calculate the packing density
        density = area[insert].sum() / np.product(extents)

        # compare this packing density against our best
        if density > best_density or count > best_count:
            best_density = density
            best_count = count
            # save the result
            result = (bounds, insert)
            # exit early if everything is inserted and
            # we have exceeded our target density
            if density > density_escape and insert.all():
                break

    return result


def images(images, power_resize=False):
    """
    Pack a list of images and return result and offsets.

    Parameters
    ------------
    images : (n,) PIL.Image
      Images to be packed
    power_resize : bool
      Should the result image be upsized to the nearest
      power of two? Not every GPU supports materials that
      aren't a power of two size.

    Returns
    -----------
    packed : PIL.Image
      Multiple images packed into result
    offsets : (n, 2) int
       Offsets for original image to pack
    """
    from PIL import Image

    # use the number of pixels as the rectangle size
    rect = np.array([i.size for i in images])

    bounds, insert = rectangles(rect=rect, rotate=False)
    # really should have inserted all the rect
    assert insert.all()

    # offsets should be integer multiple of pizels
    offset = bounds[:, 0].round().astype(int)

    extents = bounds.reshape((-1, 2)).ptp(axis=0)
    size = extents.round().astype(int)
    if power_resize:
        # round up all dimensions to powers of 2
        size = (2 ** np.ceil(np.log2(size))).astype(np.int64)

    # create the image
    result = Image.new('RGB', tuple(size))
    # paste each image into the result
    for img, off in zip(images, offset):
        result.paste(img, tuple(off))

    return result, offset
