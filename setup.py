from setuptools import find_packages, setup

setup(
	name="airfield",
	version="0.1.0",
	description="The framework for reproducible robots.",
	package_dir={"": "src"},
	packages=find_packages(where="src"),
	include_package_data=True,
	install_requires=[
		"typer>=0.9.0",
		"jinja2>=3.1.0",
		"rich>=13.0.0",
		"questionary>=2.0.0",
		"pydantic>=2.0.0",
		"pyyaml>=6.0",
	],
	entry_points={"console_scripts": ["airfield=airfield.main:app"]},
)
