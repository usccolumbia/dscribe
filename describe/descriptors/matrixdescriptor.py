from __future__ import absolute_import, division, print_function, unicode_literals
from builtins import (bytes, str, open, super, range,
                      zip, round, input, int, pow, object)
import numpy as np
from describe.descriptors import Descriptor


class MatrixDescriptor(Descriptor):
    """A common base class for two-body matrix-like descriptors.
    """
    def __init__(self, n_atoms_max, permutation="sorted_l2", sigma=None, flatten=True):
        """
        Args:
            n_atoms_max (int): The maximum nuber of atoms that any of the
                samples can have. This controls how much zeros need to be
                padded to the final result.
            permutation (string): Defines the method for handling permutational
                invariance. Can be one of the following:
                    - none: The matrix is returned in the order defined by the Atoms.
                    - sorted_l2: The rows and columns are sorted by the L2 norm.
                    - eigenspectrum: Only the eigenvalues are returned sorted
                      by their absolute value in descending order.
                    - random: ?
            flatten (bool): Whether the output of create() should be flattened
                to a 1D array.
        """
        super().__init__(flatten)

        # Check parameter validity
        if n_atoms_max <= 0:
            raise ValueError(
                "The maximum number of atoms must be a positive number."
            )
        perm_options = set(("sorted_l2", "none", "eigenspectrum", "eigenspectrum", "random"))
        if permutation not in perm_options:
            raise ValueError(
                "Unknown permutation option given. Please use one of the following: {}."
                .format(", ".join(perm_options))
            )

        if not sigma and permutation == 'random':
            raise ValueError(
                "Please specify sigma as a degree of random noise."
            )

        self.n_atoms_max = n_atoms_max
        self.permutation = permutation
        self.norm_vector = None

    def describe(self, system):
        """
        Args:
            system (System): Input system.

        Returns:
            ndarray: The zero padded Coulomb matrix either as a 2D array or as
                a 1D array depending on the setting self.flatten.
        """
        matrix = self.get_matrix(system)

        # Handle the permutation option
        if self.permutation == "none":
            pass
        elif self.permutation == "sorted_l2":
            matrix = self.sort(matrix)
        elif self.permutation == "eigenspectrum":
            matrix = self.get_eigenspectrum(matrix)
        elif self.permutation == "random":
            matrix = self.sort_randomly(matrix)
        else:
            raise ValueError(
                "Invalid permutation method: {}".format(self.permutation)
            )

        # Add zero padding
        matrix = self.zero_pad(matrix)

        # Flatten the matrix if requested
        if self.flatten:
            matrix = matrix.flatten()

        return matrix

    def sort(self, matrix):
        """Sorts the given matrix by using the L2 norm.

        Args:
            matrix(np.ndarray): The matrix to sort.

        Returns:
            np.ndarray: The sorted matrix.
        """
        # Sort the atoms such that the norms of the rows are in descending
        # order
        norms = np.linalg.norm(matrix, axis=1)
        sorted_indices = np.argsort(norms, axis=0)[::-1]
        sorted_matrix = matrix[sorted_indices]
        sorted_matrix = sorted_matrix[:, sorted_indices]

        return sorted_matrix

    def get_eigenspectrum(self, matrix):
        """Calculates the eigenvalues of the matrix and returns a list of them
        sorted by their descending absolute value.

        Args:
            matrix(np.ndarray): The matrix to sort.

        Returns:
            np.ndarray: A list of eigenvalues sorted by absolute value.
        """
        # Calculate eigenvalues
        eigenvalues, _ = np.linalg.eig(matrix)

        # Remove sign
        abs_values = np.absolute(eigenvalues)

        # Get ordering that sorts the values by absolute value
        sorted_indices = np.argsort(abs_values)[::-1]  # This sorts the list in descending order in place
        eigenvalues = eigenvalues[sorted_indices]

        return eigenvalues

    def zero_pad(self, array):
        """Zero-pads the given matrix.

        Args:
            array (np.ndarray): The array to pad

        Returns:
            np.ndarray: The zero-padded array.
        """
        # Pad with zeros
        n_atoms = array.shape[0]
        n_dim = array.ndim
        padded = np.pad(array, [(0, self.n_atoms_max-n_atoms)]*n_dim, 'constant')

        return padded

    def get_number_of_features(self):
        """Used to inquire the final number of features that this descriptor
        will have.

        Returns:
            int: Number of features for this descriptor.
        """
        if self.permutation == "eigenspectrum":
            return int(self.n_atoms_max)
        else:
            return int(self.n_atoms_max**2)

    def sort_randomly(self, matrix, sigma=1):
        """
        Given a coulomb matrix, it adds random noise to the sorting defined by sigma.
        For sorting, L2-norm is used
        """
        try:
            len(self.norm_vector)
        except:
            #self.norm_vector = np.linalg.norm(matrix, axis=1)
            self._get_norm_vector(matrix)

        noise_norm_vector = np.random.normal(self.norm_vector, sigma)
        indexlist = np.argsort(noise_norm_vector)
        indexlist = indexlist[::-1]  # order highest to lowest

        matrix = matrix[indexlist][:, indexlist]

        return matrix

    def _get_norm_vector(self, matrix):
        """
        Takes a coulomb matrix as input. Returns L2 norm of each row / column in a vector
        """
        self.norm_vector = np.linalg.norm(matrix, axis=1)

        return self.norm_vector