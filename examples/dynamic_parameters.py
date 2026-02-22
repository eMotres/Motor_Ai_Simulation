"""
Example: How to add and use dynamic parameters from motor_config.yaml

The MotorGeometryParams class is fully dynamic - it automatically reads ALL 
parameters from motor_config.yaml. No code changes needed to add new parameters!

Step 1: Add parameter to config/motor_config.yaml
--------------------------------------------------
Add to the geometry section:

  geometry:
    # ... existing parameters ...
    my_new_parameter: 42.5        # Your new parameter


Step 2: Access in motor_geometry.py
------------------------------------
Simply use self.params.YOUR_NEW_PARAMETER:

  # Example in MotorGeometry2D class
  def some_method(self):
      value = self.params.my_new_parameter  # Returns 42.5
      print(f"My value: {value}")

Step 3: Add schema metadata (optional, for web UI)
--------------------------------------------------
Add to geometry_schema section in motor_config.yaml:

  geometry_schema:
    my_new_parameter:
      label: "My New Parameter"
      unit: "mm"
      type: "float"
      min: 0
      max: 100
      step: 0.1
      group: "stator"
      description: "Description of my new parameter"

That's it! The parameter is now:
- Available in Python as self.params.my_new_parameter
- Automatically included in API responses
- Available in the web UI form (if schema is added)


Example: Using rotor_core_radius (derived property)
---------------------------------------------------
This is a @property computed from other parameters:

  @property
  def rotor_core_radius(self) -> float:
      return self.rotor_outer_radius - self.magnet_height

You can access it as: self.params.rotor_core_radius
It updates automatically when rotor_outer_radius or magnet_height changes.
"""
