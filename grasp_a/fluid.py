"""CPU Position Based Fluids, not a collection of rigid-body beads.

Equal-mass Lagrangian fluid samples solve SPH density constraints and XSPH
viscosity. Solids use finite open-cylinder wall/bottom boundaries. Boundary
momentum changes are returned for two-way coupling to the rigid vessels.
Numerical coefficients are a reproducible water-like approximation, not a
validated CFD model of a particular water temperature or surface tension.
Reference: Macklin & Mueller, Position Based Fluids, SIGGRAPH 2013.
"""
from dataclasses import dataclass
import math
import numpy as np


def rotation_matrix(quaternion):
    x, y, z, w = quaternion
    return np.array(((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)),
                     (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)),
                     (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))))


@dataclass(frozen=True)
class Vessel:
    position: tuple
    quaternion: tuple
    inner_radius: float = .032
    outer_radius: float = .035
    bottom: float = -.042
    outer_bottom: float = -.045
    rim: float = .045
    body_id: int = -1

    def local(self, points):
        return (points - self.position) @ rotation_matrix(self.quaternion)

    def contains(self, points, margin=0):
        local = self.local(points)
        return ((np.linalg.norm(local[:, :2], axis=1) <= self.inner_radius-margin)
                & (local[:, 2] >= self.bottom+margin) & (local[:, 2] <= self.rim-margin))

    def project(self, points, previous, radius):
        """Project penetrations out of finite shell/bottom; the top stays OPEN.

        Previous side disambiguates wall crossing. There is no lid, attraction
        to a target cup, fluid spawn on tilt or snapping samples into a vessel.
        """
        local, old = self.local(points), self.local(previous)
        result = local.copy()
        radial = np.linalg.norm(local[:, :2], axis=1)
        old_radial = np.linalg.norm(old[:, :2], axis=1)
        wall = ((radial > self.inner_radius-radius) & (radial < self.outer_radius+radius)
                & (local[:, 2] > self.outer_bottom-radius) & (local[:, 2] < self.rim+radius))
        inside_side = old_radial < (self.inner_radius+self.outer_radius)/2
        for mask, distance in ((wall & inside_side, self.inner_radius-radius),
                               (wall & ~inside_side, self.outer_radius+radius)):
            result[mask, :2] *= (distance/np.maximum(radial[mask], 1e-12))[:, None]
        bottom = ((radial <= self.outer_radius+radius) &
                  (local[:, 2] > self.outer_bottom-radius) & (local[:, 2] < self.bottom+radius))
        above = old[:, 2] >= (self.outer_bottom+self.bottom)/2
        result[bottom & above, 2] = self.bottom+radius
        result[bottom & ~above, 2] = self.outer_bottom-radius
        return points + (result-local) @ rotation_matrix(self.quaternion).T


class FluidNumericalError(RuntimeError):
    pass


class PositionBasedFluid:
    version = "numpy_pbf_v1"

    def __init__(self, source: Vessel, count=256, mass_kg=.1, iterations=4):
        if type(count) is not int or count < 16 or not math.isfinite(mass_kg) or mass_kg <= 0:
            raise ValueError("fluid requires positive mass and >=16 computational samples")
        if type(iterations) is not int or not 1 <= iterations <= 12:
            raise ValueError("fluid iterations must be an integer in [1,12]")
        self.count, self.initial_mass_kg = count, float(mass_kg)
        self.mass = self.initial_mass_kg/count
        self.rest_density = 1000.0
        self.spacing = (self.mass/self.rest_density)**(1/3)
        self.h = self.spacing * 1.8
        self.boundary_radius = self.spacing * .25
        self.iterations = iterations
        self.viscosity = .02  # dimensionless XSPH numerical coefficient
        self.relaxation = 100.0  # SI constraint-gradient regularizer, 1/m^2
        self.artificial_pressure = 1e-8  # SI squared-length lambda correction
        half = int(source.inner_radius/self.spacing)
        xy = [(x*self.spacing, y*self.spacing) for x in range(-half, half+1)
              for y in range(-half, half+1)
              if math.hypot(x*self.spacing, y*self.spacing) <= source.inner_radius-self.spacing*.6]
        positions = []
        if not xy:
            raise ValueError("fluid resolution/mass too coarse for this vessel")
        for layer in range(math.ceil(count/len(xy))):
            for x, y in xy:
                z = source.bottom+self.spacing*.6+layer*self.spacing
                if z >= source.rim-self.boundary_radius:
                    raise ValueError("initial fluid does not fit below source rim")
                positions.append((x, y, z))
        self.positions = np.array(positions[:count]) @ rotation_matrix(source.quaternion).T + source.position
        self.velocities = np.zeros_like(self.positions)
        self.steps = 0
        self.max_compression_seen = 0.0
        self.last_compression = 0.0
        self.last_density = np.zeros(count)
        self.boundary_impulses = {}

    def _kernels(self, positions):
        delta = positions[:, None, :] - positions[None, :, :]
        distance2 = np.einsum('ijk,ijk->ij', delta, delta)
        distance = np.sqrt(distance2)
        weights = 315/(64*math.pi*self.h**9) * np.maximum(0, self.h**2-distance2)**3
        factor = -45/(math.pi*self.h**6) * np.maximum(0, self.h-distance)**2
        gradient = delta * (factor/np.maximum(distance, 1e-10))[:, :, None]
        return weights, gradient

    def density(self):
        return self.mass*self._kernels(self.positions)[0].sum(axis=1)

    def _neighbors(self, positions):
        # Vectorized neighbour discovery; store/solve only compact-support
        # interactions, not N*N gradient/velocity tensors.
        delta = positions[:,None,:]-positions[None,:,:]
        d2 = np.einsum('ijk,ijk->ij',delta,delta)
        i,j = np.nonzero(d2 < self.h*self.h)
        distance = np.sqrt(d2[i,j])
        weight = 315/(64*math.pi*self.h**9)*(self.h*self.h-d2[i,j])**3
        factor = -45/(math.pi*self.h**6)*(self.h-distance)**2/np.maximum(distance,1e-10)
        gradient = delta[i,j]*factor[:,None]
        return i,j,weight,gradient

    def _sum_vectors(self, indices, values):
        return np.column_stack([np.bincount(indices,weights=values[:,axis],minlength=self.count)
                                for axis in range(3)])

    def step(self, dt, vessels, table_top=.60):
        if not 0 < dt <= 1/120:
            raise ValueError("fluid dt must be in (0,1/120]")
        old = self.positions.copy()
        old_velocity = self.velocities.copy()
        self.velocities[:, 2] -= 9.81*dt
        predicted = old + self.velocities*dt
        self.boundary_impulses = {}
        for _ in range(self.iterations):
            i,j,weights,gradient = self._neighbors(predicted)
            density = self.mass*np.bincount(i,weights=weights,minlength=self.count)
            constraint = np.maximum(density/self.rest_density-1, 0)
            grads = self.mass/self.rest_density*gradient
            summed = self._sum_vectors(i,grads)
            denom = np.bincount(i,weights=np.einsum('ij,ij->i',grads,grads),minlength=self.count) + np.einsum('ij,ij->i',summed,summed)
            lambdas = -constraint/(denom+self.relaxation)
            reference_weight = 315/(64*math.pi*self.h**9)*(self.h**2-(.3*self.h)**2)**3
            correction_pressure = -self.artificial_pressure*(weights/reference_weight)**4
            correction_pressure[i == j] = 0
            correction = self._sum_vectors(i,(lambdas[i]+lambdas[j]+correction_pressure)[:,None]*grads)
            # Bound iterative displacement, not physical velocity. Nonconverged
            # compression is reported rather than silently called incompressible.
            length = np.linalg.norm(correction, axis=1)
            correction *= np.minimum(1, self.spacing*.25/np.maximum(length, 1e-12))[:, None]
            predicted += correction
            for vessel in vessels:
                projected = vessel.project(predicted, old, self.boundary_radius)
                impulse = -self.mass/dt * (projected-predicted)
                self.boundary_impulses.setdefault(vessel.body_id, []).append((predicted.copy(), impulse))
                predicted = projected
            on_table = ((predicted[:, 0] >= .20) & (predicted[:, 0] <= .90)
                        & (np.abs(predicted[:, 1]) <= .45))
            predicted[on_table, 2] = np.maximum(predicted[on_table, 2], table_top+self.boundary_radius)
            predicted[:, 2] = np.maximum(predicted[:, 2], self.boundary_radius)
        self.velocities = (predicted-old)/dt
        i,j,weights,_ = self._neighbors(predicted)
        density = np.maximum(self.mass*np.bincount(i,weights=weights,minlength=self.count),self.rest_density*.1)
        # XSPH is a fluid-neighborhood velocity diffusion, not bead friction.
        velocity_delta = self.velocities[j]-self.velocities[i]
        self.velocities += self.viscosity*self._sum_vectors(i,
            (self.mass/density[j]*weights)[:,None]*velocity_delta)
        if (not np.isfinite(predicted).all() or not np.isfinite(self.velocities).all()
                or np.linalg.norm(self.velocities, axis=1).max() > 20):
            self.velocities = old_velocity
            self.boundary_impulses = {}
            raise FluidNumericalError("PBF state diverged; cannot classify this as a physical task result")
        self.positions = predicted
        self.last_density = density
        self.last_compression = float(np.maximum(density/self.rest_density-1, 0).max())
        self.max_compression_seen = max(self.max_compression_seen, self.last_compression)
        self.steps += 1

    def mass_accounting(self, source, target):
        source_mask = source.contains(self.positions)
        target_mask = target.contains(self.positions) & ~source_mask
        outside = ~(source_mask | target_mask)
        received = int(target_mask.sum())*self.mass
        retained = int(source_mask.sum())*self.mass
        spilled = int(outside.sum())*self.mass
        return dict(initial_mass_kg=self.initial_mass_kg, source_mass_kg=retained,
                    target_mass_kg=received, outside_mass_kg=spilled,
                    transfer_ratio=received/self.initial_mass_kg,
                    spill_ratio=spilled/self.initial_mass_kg,
                    accounting_error_kg=abs(self.initial_mass_kg-retained-received-spilled),
                    fluid_sample_count=self.count, max_compression=self.last_compression,
                    maximum_sample_speed_m_s=float(np.linalg.norm(self.velocities, axis=1).max()),
                    finite_state=bool(np.isfinite(self.positions).all()))

    def metadata(self):
        return dict(model="position_based_fluid", version=self.version, rigid_beads=False,
                    sample_count=self.count, total_mass_kg=self.initial_mass_kg,
                    rest_density_kg_m3=self.rest_density, smoothing_length_m=self.h,
                    constraint_iterations=self.iterations, xsph_coefficient=self.viscosity,
                    reference="https://mmacklin.com/pbf_sig_preprint.pdf",
                    limitations=["low-resolution numerical liquid; not calibrated CFD",
                                 "compression-only density constraints; no vorticity confinement",
                                 "analytic vessel boundaries; no calibrated surface tension/wetting"])
