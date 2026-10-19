# Optional integration wrappers

Put a study-specific synthesizable wrapper in `wrappers/<design>/` and add it to
`project_sources` in the design manifest. Those paths are relative to the study
root and appended after `sources`. Include wrapper packages before dependent
wrapper modules. No wrapper is required when your existing top is suitable.
