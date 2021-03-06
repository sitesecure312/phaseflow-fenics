""" **abstract_simulation.py** provides an abstract class for unsteady adaptive FEM simulations. 

We define a simulation as a sequence of time-dependent initial boundary value problems.

This module only provides an abstract base class for simulations. 
For an example of implementing a specific set of governing equations, 
see the `abstract_phasechange_simulation` module. 
For examples of implementing instantiable simulation classes,
see e.g. `cavity_melting_simulation` and `cavity_freezing_simulation`.

This module essentially contains many lessons learned by a PhD student* 
applying FEniCS to time-dependent mixed FE problems with goal-oriented AMR.
If you are not solving time-dependent problems, 
not using mixed finite elements,
or not using goal-oriented AMR, 
then perhaps you do not need to consider much of this module.

The bulk of the difficult work is done by the `fenics` module; 
but `fenics` is mostly only a library for finite element spatial discretization of PDE's.
In that context, 
little attention is paid to the time discretization of initial value problem.
Furthermore, we use some advanced features of `fenics`, 
namely mixed finite elements and goal-oriented adaptive mesh refinement (AMR),
the latter of which is not among the better supported "core features" of `fenics`.

* Alexander G. Zimmerman, AICES Graduate School, RWTH Aachen University
"""
import phaseflow
import fenics
import abc
import numpy
import matplotlib
import os


class AbstractSimulation(metaclass = abc.ABCMeta):
    """ A class for time-dependent simulations with goal-oriented AMR using FeniCS """
    def __init__(self, time_order = 1, integration_measure = fenics.dx, setup_solver = True):
    
        self.integration_measure = integration_measure
        
        self.time_order = time_order
        
        self._times = [0.,]*(time_order + 1)
        
        self._timestep_sizes = []
        
        for i in range(time_order):
        
            self._timestep_sizes.append(fenics.Constant(1.))
        
        self._mesh = self.initial_mesh()
        
        self._element = self.element()
        
        self._function_space = fenics.FunctionSpace(self._mesh, self._element)
        
        self._solutions = []
        
        for i in range(time_order + 1):
        
            self._solutions.append(fenics.Function(self.function_space))
        
        self.newton_solution = fenics.Function(self.function_space)
        
        self.adaptive_solver = None
        
        self.solver_status = {"iterations": 0, "solved": False}
        
        self.solver_needs_setup = True
        
        if setup_solver:
        
            self.setup_solver()
            
        self.output_dir = ""
    
    @property
    def timestep_size(self):
    
        return self._timestep_sizes[0]
    
    @property
    def mesh(self):
    
        return self._mesh.leaf_node()
        
    @mesh.setter
    def mesh(self, value):
        """ Automatically redefine the function space and solutions when the mesh is redefined. """
        self._mesh = value
        
        self.solver_needs_setup = True
        
        self.reinit_solutions()
        
    @property
    def function_space(self):
    
        return self._function_space.leaf_node()
        
    @property
    def solution(self):
    
        return self._solutions[0].leaf_node()
    
    @property
    def time(self):
    
        return self._times[0]
    
    @abc.abstractmethod
    def coarse_mesh(self):
        """ Redefine this to return a `fenics.Mesh`. """
        
    @abc.abstractmethod
    def element(self):
        """ Redefine this to return a `fenics.MixedElement`. """
        
    @abc.abstractmethod
    def governing_form(self):
        """ Redefine this to return a nonlinear variational form for the governing equations. """
    
    @abc.abstractmethod
    def initial_values(self):
        """ Redefine this to return a `fenics.Function` containing the initial values. """
    
    @abc.abstractmethod
    def boundary_conditions(self):
        """ Redefine this to return a list of `fenics.DirichletBC`. """
        
    def adaptive_goal(self):
        """ Redefine this to return an adaptive goal. """
        
    def initial_mesh(self):
        """ Redefine this to refine the mesh before adaptive mesh refinement. """
        return self.coarse_mesh()
        
    def reinit_solutions(self):
        """ Create the function space and solution functions for the current mesh and element. """
        self._function_space = fenics.FunctionSpace(self._mesh, self._element)
        
        for i in range(len(self._solutions)):
        
            self._solutions[i] = fenics.Function(self._function_space)
        
    def setup_solver(self):
        """ Sometimes it is necessary to set up the solver again after breaking
        important references, e.g. after re-meshing.
        """
        self._governing_form = self.governing_form()
        
        self._boundary_conditions = self.boundary_conditions()
        
        self._problem = fenics.NonlinearVariationalProblem( 
            F = self._governing_form,
            u = self.solution,
            bcs = self._boundary_conditions,
            J = fenics.derivative(
                form = self._governing_form,
                u = self.solution))
        
        save_parameters = False
        
        if hasattr(self, "solver"):
        
            save_parameters = True
        
        if save_parameters:
        
            solver_parameters = self.solver.parameters.copy()
            
        self.solver = fenics.NonlinearVariationalSolver(problem = self._problem)
            
        if save_parameters:
        
            self.solver.parameters = solver_parameters.copy()
            
        self._adaptive_goal = self.adaptive_goal()
        
        if self._adaptive_goal is not None:
        
            save_parameters = False
            
            if self.adaptive_solver is not None:
        
                save_parameters = True
            
            if save_parameters:
            
                adaptive_solver_parameters = self.adaptive_solver.parameters.copy()
            
            self.adaptive_solver = fenics.AdaptiveNonlinearVariationalSolver(
                problem = self._problem,
                goal = self._adaptive_goal)
            
            if save_parameters:
        
                self.adaptive_solver.parameters = adaptive_solver_parameters.copy()
        
        self.solver_needs_setup = False
        
    """ The following methods are used to solve time steps and advance the unsteady simulation. """
    def solve(self, goal_tolerance = None):
        """ Solve the nonlinear variational problem.
        Optionally provide `goal_tolerance` to use the adaptive solver. 
        """
        if self.solver_needs_setup:
        
            self.setup_solver()
            
        self._times[0] = self._times[1] + self.timestep_size.__float__()
        
        if goal_tolerance is None:
        
            solver_status = self.solver.solve()
            
            self.solver_status["iterations"] = solver_status[0]
            
        else:
            
            share_solver_parameters(
                self.adaptive_solver.parameters["nonlinear_variational_solver"],
                self.solver.parameters)
                    
            self.adaptive_solver.solve(goal_tolerance)
            
            """ `fenics.AdaptiveNonlinearVariationalSolver` does not return status."""
            self.solver_status["iterations"] = "NA"
            
        self.solver_status["solved"] = True
        
        return self.solver_status
        
    def advance(self):
        """ Move solutions backward in the queue to prepare for a new time step. 
        This is a separate method, since one may want to call `solve` multiple times
        before being satisfied with the solution state.
        """
        if self.time_order > 1:
            
            self._solutions[2].leaf_node().assign(self._solutions[1].leaf_node())
        
            self._times[2] = 0. + self._times[1]
            
            self._timestep_sizes[1].assign(self._timestep_sizes[0])
            
        self._solutions[1].leaf_node().assign(self._solutions[0].leaf_node())
        
        self._times[1] = 0. + self._times[0]
        
    """ The following are some utility methods. """
    def time_discrete_terms(self):
        """ Apply first-order implicit Euler finite difference method. """
        wnp1 = fenics.split(self._solutions[0].leaf_node())
        
        wn = fenics.split(self._solutions[1].leaf_node())
        
        if self.time_order == 1:
        
            return tuple([
                phaseflow.backward_difference_formulas.apply_backward_euler(
                    self._timestep_sizes[0], 
                    (wnp1[i], wn[i])) 
                for i in range(len(wn))])
        
        if self.time_order > 1:
        
            wnm1 = fenics.split(self._solutions[2].leaf_node())
            
        if self.time_order == 2:
        
            return tuple([
                phaseflow.backward_difference_formulas.apply_bdf2(
                    (self._timestep_sizes[0], self._timestep_sizes[1]), 
                    (wnp1[i], wn[i], wnm1[i])) 
                for i in range(len(wn))])
            
        if self.time_order > 2:
        
            raise NotImplementedError()
            
    def assign_initial_values(self):
        """ Set values of all solutions from `self.initial_values()`. """
        initial_values = self.initial_values()
        
        for i in range(len(self._solutions)):
        
            self._solutions[i].assign(initial_values)
        
    def reset_initial_guess(self):
        """ Set the values of the latest solution from the next latest solution. """
        self._solutions[0].leaf_node().vector()[:] = self._solutions[1].leaf_node().vector()
        
    def save_newton_solution(self):
        """ When not using AMR, we can save a copy of the solution from the latest successful Newton iteration.
        This can be useful, since a failed Newton iteration will blow up the solution, replacing it with garbage.
        
        This will fail if the mesh has been changed by the adaptive solver 
        and `self.newton_solution` has not been reinitialized with 
        `self.newton_solution = fenics.Function(self.function_space)`.
        """
        self.newton_solution.vector()[:] = self._solutions[0].vector()
        
    def load_newton_solution(self):
        """ When not using AMR, we can load a copy of the solution from the latest successful Newton iteration.
        This can be useful, since a failed Newton iteration will blow up the solution, replacing it with garbage.
        
        This will fail if the mesh has been changed by the adaptive solver 
        and `self.newton_solution` has not been reinitialized with 
        `self.newton_solution = fenics.Function(self.function_space)`.
        """
        self._solutions[0].vector()[:] = self.newton_solution.vector()
        
    def set_solution_on_subdomain(self, subdomain, values):
        """ Abuse `fenics.DirichletBC` to set values of a function on a subdomain. 
        
        Parameters
        ----------
        subdomain
        
            `fenics.SubDomain`
            
        values
        
            container of objects that would typically be passed to 
            `fenics.DirichletBC` as the values of the boundary condition,
            one for each subspace of the mixed finite element solution space
        """
        function_space = fenics.FunctionSpace(self.mesh.leaf_node(), self.element())

        new_solution = fenics.Function(function_space)

        new_solution.vector()[:] = self.solution.vector()
        
        for function_subspace_index in range(len(fenics.split(self.solution))):
        
            hack = fenics.DirichletBC(
                function_space.sub(function_subspace_index),
                values[function_subspace_index],
                subdomain)

            hack.apply(new_solution.vector())
        
        self.solution.vector()[:] = new_solution.vector()
    
    def deepcopy(self):
        """ Return an entire deep copy of `self`. 
        For example, this is useful for checkpointing small problems in memory,
        or for running a batch of simulations with parameter changes.
        """
        sim = type(self)(
            time_order = self.time_order, 
            integration_measure = self.integration_measure(),
            setup_solver = False)
        
        sim._mesh = fenics.Mesh(self.mesh)
        
        sim._function_space = fenics.FunctionSpace(sim.mesh, sim._element)
        
        for i in range(len(self._solutions)):
            
            sim._solutions[i] = fenics.Function(sim.function_space)
            
            sim._solutions[i].leaf_node().vector()[:] = self._solutions[i].leaf_node().vector()
            
            sim._times[i] = 0. + self._times[i]
        
        for i in range(len(self._timestep_sizes)):
        
            sim._timestep_sizes[i] = self._timestep_sizes[i]
        
        sim.setup_solver()
        
        sim.solver.parameters = self.solver.parameters.copy()
        
        return sim
        
    def print_constants(self):
        """ Print the names and values of all `fenics.Constant` attributes. 
        For example, this is useful for verifying that the correct parameters
        have been set. 
        """
        for key in self.__dict__.keys():
    
            attribute = self.__dict__[key]
            
            if type(attribute) is type(fenics.Constant(0.)):
                
                print(attribute.name() + " = " + str(attribute.values()))


    def write_checkpoint(self, filepath):
        """Write solutions, times, and timestep sizes to a checkpoint file."""
        print("Writing checkpoint to " + filepath)
        
        with fenics.HDF5File(self.mesh.mpi_comm(), filepath, "w") as h5:
            
            h5.write(self._solutions[0].function_space().mesh().leaf_node(), "mesh")
        
            for i in range(len(self._solutions)):
            
                h5.write(self._solutions[i].leaf_node(), "solution" + str(i))
            
                """ The fenics.HDF5File interface does not allow us to write floats,
                but rather only a numpy array. """
                h5.write(numpy.array((self._times[i],)), "time" + str(i))
               
               
    def read_checkpoint(self, filepath):
        """Read solutions and times from a checkpoint file."""
        self._mesh = fenics.Mesh()
        
        print("Reading checkpoint from " + filepath)
            
        with fenics.HDF5File(self.mesh.mpi_comm(), filepath, "r") as h5:
        
            h5.read(self._mesh, "mesh", True)
        
            self._function_space = fenics.FunctionSpace(self.mesh, self._element)
            
            for i in range(self.time_order + 1):
            
                self._solutions[i] = fenics.Function(self.function_space)
            
                h5.read(self._solutions[i], "solution" + str(i))
            
                """ fenics.HDF5File doesn't implement read methods for every write method.
                Our only option here seems to be to use a fenics.Vector to store values,
                because a reader is implemented for GenericVector, which Vector inherits from.
                Furthermore, for the correct read method to be called, we must pass a boolean
                as a third argument related to distributed memory.
                """
                time = fenics.Vector(fenics.mpi_comm_world(), 1)
                
                h5.read(time, "time" + str(i), False)
                
                self._times[i] = time.get_local()[0]
        
        self.newton_solution = fenics.Function(self.function_space)
        
        self.setup_solver()
        
    def write_solution(self, file, solution_index = 0):
        """ Write the solution to a file.
        Parameters
        ----------
        file : fenics.XDMFFile
            This method should have been called from within the context of the open `file`.
        """
        print("Writing solution to " + file.path)
        
        for var in self._solutions[solution_index].leaf_node().split():

            file.write(var, self._times[solution_index])
            
    def convert_checkpoints_to_xdmf_solution(self, checkpoint_dir, xdmf_solution_filepath):
    
        with phaseflow.helpers.SolutionFile(xdmf_solution_filepath) as xdmf_solution_file:
        
            for filename in os.listdir(checkpoint_dir):
            
                if ("checkpoint" in filename) and filename.endswith(".h5"):
                
                    self.read_checkpoint(checkpoint_dir + "/" + filename)
        
                    self.write_solution(xdmf_solution_file)
            
    def plot(self, solution_index = 0, savefigs = False):
        """ Plot the adaptive mesh and all parts of the mixed finite element solution. """
        if not (self.output_dir == ""):
        
            phaseflow.helpers.mkdir_p(self.output_dir)
        
        self._plot(
            solution = self._solutions[solution_index], 
            time = self._times[solution_index],
            savefigs = savefigs)
        
    def _plot(self, solution, time, savefigs = False):

        phaseflow.plotting.plot(solution.function_space().mesh().leaf_node())
        
        matplotlib.pyplot.title("$\Omega_h, t = " + str(time) + "$")
        
        matplotlib.pyplot.xlabel("$x$")
        
        matplotlib.pyplot.ylabel("$y$")
        
        if savefigs:
        
            matplotlib.pyplot.savefig(fname = self.output_dir + "mesh_t" + str(time) + ".png")
        
        matplotlib.pyplot.show()
        
        w = solution.leaf_node().split()
        
        for i in range(len(w)):

            some_mappable_thing = phaseflow.plotting.plot(w[i])
            
            matplotlib.pyplot.colorbar(some_mappable_thing)
            
            matplotlib.pyplot.title("$w_" + str(i) + ", t = " + str(time) + "$")
            
            matplotlib.pyplot.xlabel("$x$")
            
            matplotlib.pyplot.ylabel("$y$")
            
            if savefigs:
            
                matplotlib.pyplot.savefig(fname = self.output_dir + "w" + str(i) + "_t" + str(time) + ".png")
            
            matplotlib.pyplot.show()
        
        
def share_solver_parameters(share_to_parameters, share_from_parameters):
    """ FEniCS implements a setter for the solver parameters which does not allow us to
        
        `adaptive_solver.parameters["nonlinear_variational_solver"] = solver.parameters`
    
    so we recursively catch the resulting KeyError exception and set all parameters.
    """
    for key in share_from_parameters:
    
        try: 
        
            share_to_parameters[key] = share_from_parameters[key]
                
        except KeyError:
        
            share_solver_parameters(share_to_parameters[key], share_from_parameters[key])
