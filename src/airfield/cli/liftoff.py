import typer
import subprocess
from airfield.models import Plan
from airfield.config import plans_dir, require_project_root

def run(plan_name: str):
    print(f"Loading plan {plan_name}...")

    root = require_project_root()
    plan_yaml = plans_dir(root) / f"{plan_name}.yaml"
    
    if not plan_yaml.exists():
        print(f"Error: Plan {plan_name} not found at {plan_yaml}")
        raise typer.Exit(1)
        
    plan = Plan.load(plan_yaml)
    
    print(f"Launching plan {plan.name} with packages: {', '.join(plan.packages)}")
    
    print("Orchestrating packages via Airfield project run...")
    for pkg in plan.packages:
        print(f" -> Launching {pkg}...")
        result = subprocess.run(["airfield", "project", "run", pkg])
        if result.returncode != 0:
            print(f"Error: failed to launch {pkg}")
            raise typer.Exit(1)
        
    print("Liftoff complete. Packages are running.")

if __name__ == "__main__":
    typer.run(run)
