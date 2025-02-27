import numpy as np
import porepy as pp
import scipy.sparse as sps

"""
Acknowledgments:
    The functionalities related to the ridge computations are modified from
    github.com/anabudisa/md_aux_precond developed by Ana Budiša and Wietse M. Boon.
"""


class Grid(pp.Grid):
    def __init__(self, *args, **kwargs):
        super(Grid, self).__init__(*args, **kwargs)

    def compute_geometry(self):
        """
        Defines grid entities of codim 2 and 3.

        The entities are referred to by their codimension:
        0: "cells"
        1: "faces"
        2: "ridges"
        3: "peaks"
        """

        super(Grid, self).compute_geometry()
        self.compute_ridges()

    def compute_ridges(self):
        """
        Assigns the following attributes to the grid

        num_ridges: number of ridges
        num_peaks: number of peaks
        face_ridges: connectivity between each face and ridge
        ridge_peaks: connectivity between each ridge and peak
        tags['tip_ridges'], tags['tip_peaks']: tags for entities at fracture tips
        """

        if self.dim == 3:
            self._compute_ridges_3d()
        elif self.dim == 2:
            self._compute_ridges_2d()
        else:  # The grid is of dimension 0 or 1.
            self._compute_ridges_01d()

        self.tag_ridges()

    def _compute_ridges_01d(self):
        """
        Assign the number of ridges, number of peaks, and connectivity matrices to a
        grid of dimension 0 or 1.
        """

        self.num_peaks = 0
        self.num_ridges = 0
        self.ridge_peaks = sps.csc_matrix((self.num_peaks, self.num_ridges), dtype=int)
        self.face_ridges = sps.csc_matrix((self.num_ridges, self.num_faces), dtype=int)

    def _compute_ridges_2d(self):
        """
        Assign the number of ridges, number of peaks, and connectivity matrices to a
        grid of dimension 2.
        """

        self.num_peaks = 0
        self.num_ridges = self.num_nodes
        self.ridge_peaks = sps.csc_matrix((self.num_peaks, self.num_ridges), dtype=int)

        # We compute the face tangential by mapping the face normal to a reference grid
        # in the xy-plane, rotating locally, and mapping back.
        R = pp.map_geometry.project_plane_matrix(self.nodes)
        loc_rot = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        rot = R.T @ loc_rot @ R
        rotated_normal = rot.dot(self.face_normals)

        # The face-ridge orientation is determined by whether the rotated normal
        # coincides with the difference vector between the ridges.
        face_ridges = self.face_nodes.copy().astype(int)
        face_ridges.data[::2] *= -1
        face_tangents = self.nodes @ face_ridges

        orients = np.sign(np.sum(rotated_normal * face_tangents, axis=0))

        self.face_ridges = face_ridges * sps.diags(orients)

    def _compute_ridges_3d(self):
        """
        Assign the number of ridges, number of peaks, and connectivity matrices to a
        grid of dimension 3.
        """

        self.num_peaks = self.num_nodes

        # Pre-allocation
        ridges = np.ndarray((2, self.face_nodes.nnz), dtype=int)

        fr_indptr = np.zeros(self.num_faces + 1, dtype=int)
        for face in np.arange(self.num_faces):
            # find indices for nodes of this face
            loc = self.face_nodes.indices[
                self.face_nodes.indptr[face] : self.face_nodes.indptr[face + 1]
            ]
            fr_indptr[face + 1] = fr_indptr[face] + loc.size

            # Define ridges between each pair of nodes
            # assuming ordering in face_nodes is done
            # according to right-hand rule
            ridges[:, fr_indptr[face] : fr_indptr[face + 1]] = np.row_stack(
                (loc, np.roll(loc, -1))
            )

        # Save orientation of each ridge w.r.t. the face
        orientations = np.sign(ridges[1, :] - ridges[0, :])

        # Ridges are oriented from low to high node indices
        ridges.sort(axis=0)
        ridges, _, indices = pp.utils.setmembership.unique_columns_tol(ridges)
        self.num_ridges = np.size(ridges, 1)

        # Generate ridge-peak connectivity such that
        # ridge_peaks(i, j) = +/- 1:
        # ridge j points to/away from peak i
        indptr = np.arange(0, ridges.size + 1, 2)
        ind = np.ravel(ridges, order="F")
        data = -((-1) ** np.arange(ridges.size))
        self.ridge_peaks = sps.csc_matrix((data, ind, indptr))

        # Generate face_ridges such that
        # face_ridges(i, j) = +/- 1:
        # face j has ridge i with same/opposite orientation
        # with the orientation defined according to the right-hand rule
        self.face_ridges = sps.csc_matrix((orientations, indices, fr_indptr))

    def tag_ridges(self):
        """
        Tag the peaks and ridges of the grid located on fracture tips.
        """

        self.tags["tip_peaks"] = np.zeros(self.num_peaks, dtype=bool)
        fr_bool = self.face_ridges.astype("bool")

        if self.dim == 2:
            self.tags["tip_ridges"] = fr_bool * self.tags["tip_faces"]
        else:
            self.tags["tip_ridges"] = np.zeros(self.num_ridges, dtype=bool)

        bd_ridges = fr_bool * self.tags["domain_boundary_faces"]
        self.tags["domain_boundary_ridges"] = bd_ridges.astype(bool)

    def compute_subvolumes(self, return_subsimplices=False):
        """
        Return the following attributes of the grid

        subvolumes: a csc_matrix with each entry [node, cell] describing
                      the signed measure of the associated sub-volume
        """
        sub_simplices = self.cell_faces.copy().astype(float)

        faces, cells, orient = sps.find(self.cell_faces)

        normals = self.face_normals[:, faces] * orient
        rays = self.face_centers[:, faces] - self.cell_centers[:, cells]

        sub_simplices[faces, cells] = np.sum(normals * rays, 0) / self.dim

        nodes_per_face = np.array(np.sum(self.face_nodes, 0)).flatten()
        div_by_nodes_per_face = sps.diags(1.0 / nodes_per_face)

        if return_subsimplices:
            return (
                self.face_nodes @ div_by_nodes_per_face @ sub_simplices,
                sub_simplices,
            )
        else:
            return self.face_nodes @ div_by_nodes_per_face @ sub_simplices
