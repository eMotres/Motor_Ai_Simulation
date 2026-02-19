# Electric Motor AI Simulator

Physics-Informed Neural Network for Electric Motor Simulation using PhysicsNeMo.

## Features

- 🧲 Parametric 2D/3D geometry of BLDC/PMSM motors
- 🔧 Material definitions (electrical steel, permanent magnets, copper, air)
- 📊 Mesh generation with material properties
- 🧠 Physics-Informed Neural Networks for electromagnetic simulation
- 📈 Visualization tools for motor geometry and fields

## Installation

### Prerequisites

- Python 3.10+
- PyTorch 2.0+
- PhysicsNeMo

### Install PhysicsNeMo

```bash
# From local installation (recommended for development)
pip install -e /path/to/modulus

# Or from GitHub
pip install git+https://github.com/NVIDIA/physicsnemo.git
```

### Install Motor AI Simulator

```bash
# From source
cd motor_ai_sim
pip install -e .

# With development dependencies
pip install -e ".[dev]"
```

## Quick Start

```python
from motor_ai_sim.geometry import MotorGeometryParams, MotorMeshGenerator
from motor_ai_sim.utils import visualize_motor

# Create motor geometry
params = MotorGeometryParams(
    stator_outer_radius=0.05,
    stator_inner_radius=0.03,
    rotor_outer_radius=0.028,
    num_slots=12,
    num_poles=4,
)

# Generate mesh with materials
generator = MotorMeshGenerator(params)
meshes = generator.generate()

# Visualize
visualize_motor(meshes)
```

## Project Structure

```
motor_ai_sim/
├── src/motor_ai_sim/
│   ├── geometry/
│   │   ├── materials.py         # Material definitions
│   │   ├── motor_geometry.py    # Parametric geometry
│   │   └── mesh_generator.py    # Mesh generation
│   └── utils/
│       └── visualization.py     # Plotting utilities
├── config/
│   └── motor_config.yaml        # Motor parameters
├── examples/
│   └── visualize_motor.py       # Example script
└── tests/
    └── test_geometry.py         # Unit tests
```

## Motor Components

| Component | Description | Material |
|-----------|-------------|----------|
| Stator Core | Laminated electrical steel | M-27 Silicon Steel |
| Stator Slots | Copper windings | Copper |
| Air Gap | Air between stator and rotor | Air |
| Rotor Core | Solid or laminated steel | M-27 Silicon Steel |
| Permanent Magnets | NdFeB magnets | N42SH |
| Shaft | Steel shaft | Carbon Steel |

## Physics

The simulator solves Maxwell's equations for magnetostatics:

- **∇ × H = J** (Ampère's Law)
- **∇ · B = 0** (No magnetic monopoles)
- **B = μH** (Constitutive relation)

Using vector potential **A** where **B = ∇ × A**:

- **∇²A = -μJ** (Poisson equation)

## License

MIT License

## References

- [PhysicsNeMo Documentation](https://nvidia.github.io/physicsnemo/)
- [Maxwell's Equations](https://en.wikipedia.org/wiki/Maxwell%27s_equations)
- [BLDC Motor Design](https://en.wikipedia.org/wiki/Brushless_DC_electric_motor)
