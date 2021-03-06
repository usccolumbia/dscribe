# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function
from builtins import super
import numpy as np

from joblib import Parallel, delayed, parallel_backend

from scipy.sparse import coo_matrix

from ase import Atoms

from dscribe.descriptors import Descriptor
from dscribe.core import System

import soaplite


class SOAP(Descriptor):
    """Class for the Smooth Overlap of Atomic Orbitals (SOAP) descriptor. This
    implementation uses orthogonalized spherical primitive gaussian type
    orbitals as the radial basis set to reach a fast analytical solution.

    For reference, see:

    "On representing chemical environments, Albert P. Bartók, Risi Kondor, and
    Gábor Csányi, Phys. Rev. B 87, 184115, (2013),
    https://doi.org/10.1103/PhysRevB.87.184115

    "Comparing molecules and solids across structural and alchemical space",
    Sandip De, Albert P. Bartók, Gábor Csányi and Michele Ceriotti, Phys.
    Chem. Chem. Phys. 18, 13754 (2016), https://doi.org/10.1039/c6cp00415f
    """
    def __init__(
            self,
            rcut,
            nmax,
            lmax,
            sigma=1.0,
            rbf="gto",
            species=None,
            atomic_numbers=None,
            periodic=False,
            crossover=True,
            average=False,
            sparse=True
            ):
        """
        Args:
            rcut (float): A cutoff for local region in angstroms. Should be
                bigger than 1 angstrom.
            nmax (int): The number of radial basis functions.
            lmax (int): The maximum degree of spherical harmonics.
            species (iterable): The chemical species as a list of atomic
                numbers or as a list of chemical symbols. Notice that this is not
                the atomic numbers that are present for an individual system, but
                should contain all the elements that are ever going to be
                encountered when creating the descriptors for a set of systems.
                Keeping the number of chemical species as low as possible is
                preferable.
            atomic_numbers (iterable): A list of the atomic numbers that should
                be taken into account in the descriptor. Deprecated in favour of
                the species-parameters, but provided for
                backwards-compatibility.
            sigma (float): The standard deviation of the gaussians used to expand the
                atomic density.
            rbf (str): The radial basis functions to use. The available options are:

                * "gto": Spherical gaussian type orbitals defined as :math:`g_{nl}(r) = \sum_{n'=1}^{n_\mathrm{max}}\,\\beta_{nn'l} r^l e^{-\\alpha_{n'l}r^2}`
                * "polynomial": Polynomial basis defined as :math:`g_{n}(r) = \sum_{n'=1}^{n_\mathrm{max}}\,\\beta_{nn'} (r-r_\mathrm{cut})^{n'+2}`

            periodic (bool): Determines whether the system is considered to be
                periodic.
            crossover (bool): Default True, if crossover of atomic types should
                be included in the power spectrum.
            average (bool): Whether to build an average output for all selected
                positions.
            sparse (bool): Whether the output should be a sparse matrix or a
                dense numpy array.
        """
        super().__init__(flatten=True, sparse=sparse)

        # Setup the involved chemical species
        species = self.get_species_definition(species, atomic_numbers)
        self.species = species

        # Check that sigma is valid
        if (sigma <= 0):
            raise ValueError(
                "Only positive gaussian width parameters 'sigma' are allowed."
            )
        self._eta = 1/(2*sigma**2)

        # Check that rcut is valid
        if rbf == "gto" and rcut <= 1:
            raise ValueError(
                "When using the gaussian radial basis set (gto), the radial "
                "cutoff should be bigger than 1 angstrom."
            )

        supported_rbf = set(("gto", "polynomial"))
        if rbf not in supported_rbf:
            raise ValueError(
                "Invalid radial basis function of type '{}' given. Please use "
                "one of the following: {}".format(rbf, supported_rbf)
            )

        # Crossover cannot be disabled on poly rbf
        if not crossover and rbf == "polynomial":
            raise ValueError(
                "Disabling crossover is not currently supported when using "
                "polynomial radial basis function".format(rbf, supported_rbf)
            )

        self._rcut = rcut
        self._nmax = nmax
        self._lmax = lmax
        self._rbf = rbf
        self._periodic = periodic
        self._crossover = crossover
        self._average = average

        if self._rbf == "gto":
            self._alphas, self._betas = soaplite.genBasis.getBasisFunc(self._rcut, self._nmax)

    def create(self, system, positions=None, n_jobs=1, verbose=False):
        """Return the SOAP output for the given systems and given positions.

        Args:
            system (single or multiple class:`ase.Atoms`): One or many atomic structures.
            positions (list): Positions where to calculate SOAP. Can be
                provided as cartesian positions or atomic indices. If no
                positions are defined, the SOAP output will be created for all
                atoms in the system. When calculating SOAP for multiple
                systems, provide the positions as a list for each system.
            n_jobs (int): Number of parallel jobs to instantiate. Parallellizes
                the calculation across samples. Defaults to serial calculation
                with n_jobs=1.
            verbose(bool): Controls whether to print the progress of each job
                into to the console.

        Returns:
            np.ndarray | scipy.sparse.csr_matrix: The SOAP output for the given
            systems and positions. The return type depends on the
            'sparse'-attribute. The first dimension is determined by the amount
            of positions and systems and the second dimension is determined by
            the get_number_of_features()-function. When multiple systems are
            provided the results are ordered by the input order of systems and
            their positions.
        """
        # If single system given, skip the parallelization
        if isinstance(system, (Atoms, System)):
            return self.create_single(system, positions)

        # Combine input arguments
        n_samples = len(system)
        if positions is None:
            inp = [(i_sys,) for i_sys in system]
        else:
            n_pos = len(positions)
            if n_pos != n_samples:
                raise ValueError(
                    "The given number of positions does not match the given"
                    "number os systems."
                )
            inp = list(zip(system, positions))

        # For SOAP the output size for each job depends on the exact arguments.
        # Here we precalculate the size for each job to preallocate memory and
        # make the process faster.
        k, m = divmod(n_samples, n_jobs)
        jobs = (inp[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n_jobs))
        output_sizes = []
        for i_job in jobs:
            n_desc = 0
            if self._average:
                n_desc = len(i_job)
            elif positions is None:
                n_desc = 0
                for job in i_job:
                    n_desc += len(job[0])
            else:
                n_desc = 0
                for i_sample, i_pos in i_job:
                    if i_pos is not None:
                        n_desc += len(i_pos)
                    else:
                        n_desc += len(i_sample)
            output_sizes.append(n_desc)

        # Create in parallel
        output = self.create_parallel(inp, self.create_single, n_jobs, output_sizes, verbose=verbose)

        return output

    def create_single(self, system, positions=None):
        """Return the SOAP output for the given system and given positions.

        Args:
            system (:class:`ase.Atoms` | :class:`.System`): Input system.
            positions (list): Cartesian positions or atomic indices. If
                specified, the SOAP spectrum will be created for these points.
                If no positions are defined, the SOAP output will be created
                for all atoms in the system.

        Returns:
            np.ndarray | scipy.sparse.coo_matrix: The SOAP output for the
            given system and positions. The return type depends on the
            'sparse'-attribute. The first dimension is given by the number of
            positions and the second dimension is determined by the
            get_number_of_features()-function.
        """
        # Transform the input system into the internal System-object
        system = self.get_system(system)

        # Check that the system does not have elements that are not in the list
        # of atomic numbers
        self.check_atomic_numbers(system.get_atomic_numbers())
        sub_elements = np.array(list(set(system.get_atomic_numbers())))

        # Check if periodic is valid
        if self._periodic:
            cell = system.get_cell()
            if np.cross(cell[0], cell[1]).dot(cell[2]) == 0:
                raise ValueError(
                    "System doesn't have cell to justify periodicity."
                )

        # Positions specified, use them
        if positions is not None:

            # Check validity of position definitions and create final cartesian
            # position list
            list_positions = []
            if len(positions) == 0:
                raise ValueError(
                    "The argument 'positions' should contain a non-empty set of"
                    " atomic indices or cartesian coordinates with x, y and z "
                    "components."
                )
            for i in positions:
                if np.issubdtype(type(i), np.integer):
                    list_positions.append(system.get_positions()[i])
                elif isinstance(i, list) or isinstance(i, tuple):
                    if len(i) != 3:
                        raise ValueError(
                            "The argument 'positions' should contain a "
                            "non-empty set of atomic indices or cartesian "
                            "coordinates with x, y and z components."
                        )
                    list_positions.append(i)
                else:
                    raise ValueError(
                        "Create method requires the argument 'positions', a "
                        "list of atom indices and/or positions"
                    )

            # Determine the SOAPLite function to call based on periodicity and
            # rbf
            if self._rbf == "gto":
                if self._periodic:
                    soap_func = soaplite.get_periodic_soap_locals
                else:
                    soap_func = soaplite.get_soap_locals
                soap_mat = soap_func(
                    system,
                    list_positions,
                    self._alphas,
                    self._betas,
                    rCut=self._rcut,
                    nMax=self._nmax,
                    Lmax=self._lmax,
                    crossOver=self._crossover,
                    all_atomtypes=None,
                    eta=self._eta
                )
            elif self._rbf == "polynomial":
                if self._periodic:
                    soap_func = soaplite.get_periodic_soap_locals_poly
                else:
                    soap_func = soaplite.get_soap_locals_poly
                soap_mat = soap_func(
                    system,
                    list_positions,
                    rCut=self._rcut,
                    nMax=self._nmax,
                    Lmax=self._lmax,
                    all_atomtypes=None,
                    eta=self._eta
                )

        # No positions given, calculate SOAP for all atoms in the structure
        else:
            # Determine the SOAPLite function to call based on periodicity and
            # rbf
            if self._rbf == "gto":
                if self._periodic:
                    soap_func = soaplite.get_periodic_soap_structure
                else:
                    soap_func = soaplite.get_soap_structure
                soap_mat = soap_func(
                    system,
                    self._alphas,
                    self._betas,
                    rCut=self._rcut,
                    nMax=self._nmax,
                    Lmax=self._lmax,
                    crossOver=self._crossover,
                    all_atomtypes=None,
                    eta=self._eta
                )
            elif self._rbf == "polynomial":
                if self._periodic:
                    soap_func = soaplite.get_periodic_soap_structure_poly
                else:
                    soap_func = soaplite.get_soap_structure_poly
                soap_mat = soap_func(
                    system,
                    rCut=self._rcut,
                    nMax=self._nmax,
                    Lmax=self._lmax,
                    all_atomtypes=None,
                    eta=self._eta
                )

        # Map the output from subspace of elements to the full space of
        # elements
        soap_mat = self.get_full_space_output(
            soap_mat,
            sub_elements,
            self._atomic_numbers
        )

        # Create the averaged SOAP output if requested.
        if self._average:
            soap_mat = soap_mat.mean(axis=0)
            soap_mat = np.expand_dims(soap_mat, 0)

        # Make into a sparse array if requested
        if self._sparse:
            soap_mat = coo_matrix(soap_mat)

        return soap_mat

    @property
    def species(self):
        return self._species

    @species.setter
    def species(self, value):
        """Used to check the validity of given atomic numbers and to initialize
        the C-memory layout for them.

        Args:
            value(iterable): Chemical species either as a list of atomic
                numbers or list of chemical symbols.
        """
        # The species are stored as atomic numbers for internal use.
        self._set_species(value)

    def get_full_space_output(self, sub_output, sub_elements, full_elements_sorted):
        """Used to partition the SOAP output to different locations depending
        on the interacting elements. SOAPLite return the output partitioned by
        the elements present in the given system. This function correctly
        places those results within a bigger chemical space.

        Args:
            sub_output(np.ndarray): The output fron SOAPLite
            sub_elements(list): The atomic numbers present in the subspace
            full_elements_sorted(list): The atomic numbers present in the full
                space, sorted.

        Returns:
            np.ndarray: The given SOAP output mapped to the full chemical space.
        """
        # Get mapping between elements in the subspace and alements in the full
        # space
        space_map = self.get_sub_to_full_map(sub_elements, full_elements_sorted)

        # Reserve space for a sparse matric containing the full output space
        n_features = self.get_number_of_features()
        n_elem_features = self.get_number_of_element_features()
        n_points = sub_output.shape[0]

        # Define the final output space as an array.
        output = np.zeros((n_points, n_features), dtype=np.float32)

        n_elem_sub = len(sub_elements)
        n_elem_full = len(full_elements_sorted)
        for i_sub in range(n_elem_sub):
            for j_sub in range(n_elem_sub):
                if j_sub >= i_sub:

                    # This is the index of the spectrum. It is given by enumerating the
                    # elements of an upper triangular matrix from left to right and top
                    # to bottom.
                    m = self.get_flattened_index(i_sub, j_sub, n_elem_sub)
                    start_sub = m*n_elem_features
                    end_sub = (m+1)*n_elem_features
                    sub_out = sub_output[:, start_sub:end_sub]

                    # Figure out position in the full element space
                    i_full = space_map[i_sub]
                    j_full = space_map[j_sub]
                    m_full = self.get_flattened_index(i_full, j_full, n_elem_full)

                    # Place output to full output vector
                    start_full = m_full*n_elem_features
                    end_full = (m_full+1)*n_elem_features
                    output[:, start_full:end_full] = sub_out

        return output

    def get_sub_to_full_map(self, sub_elements, full_elements):
        """Used to map an index in the sub-space of elements to the full
        element-space.
        """
        # Sort the elements according to atomic number
        sub_elements_sorted = np.sort(sub_elements)
        full_elements_sorted = np.sort(full_elements)

        mapping = {}
        for i_sub, z in enumerate(sub_elements_sorted):
            i_full = np.where(full_elements_sorted == z)[0][0]
            mapping[i_sub] = i_full

        return mapping

    def get_number_of_element_features(self):
        """Used to query the number of elements in the SOAP feature space for
        a single element pair.

        Returns:
            int: The number of features per element pair.
        """
        return int((self._lmax + 1) * self._nmax * (self._nmax + 1)/2)

    def get_flattened_index(self, i, j, n):
        """Returns the 1D index of an element in an upper diagonal matrix that
        has been flattened by iterating over the elements from left to right
        and top to bottom.
        """
        return int(j + i*n - i*(i+1)/2)

    def get_number_of_features(self):
        """Used to inquire the final number of features that this descriptor
        will have.

        Returns:
            int: Number of features for this descriptor.
        """
        n_elems = len(self._atomic_numbers)
        if self._crossover:
            n_blocks = n_elems * (n_elems + 1)/2
        else:
            n_blocks = n_elems

        n_element_features = self.get_number_of_element_features()

        return int(n_element_features * n_blocks)
