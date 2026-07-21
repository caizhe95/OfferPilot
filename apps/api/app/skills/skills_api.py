"""Skills API endpoints."""

from fastapi import APIRouter, HTTPException
from app.skills.skills_loader import (
    list_skills,
    load_skill,
    load_skill_references,
    match_skill,
    validate_all_skills,
)

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
async def list_skills_endpoint():
    """List all available skills."""
    skills = list_skills()
    return {"skills": skills}


@router.get("/{skill_name}")
async def get_skill_endpoint(skill_name: str):
    """Get a specific skill by name."""
    skill = load_skill(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    return skill


@router.get("/{skill_name}/references")
async def get_skill_references_endpoint(skill_name: str):
    """Get references for a specific skill."""
    skill = load_skill(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    refs = load_skill_references(skill_name)
    return {"skill": skill_name, "references": refs}


@router.post("/match")
async def match_skill_endpoint(input: dict):
    """Match user input to the most appropriate skill."""
    user_input = input.get("input", "")
    if not user_input:
        raise HTTPException(status_code=400, detail="Input is required")

    skill = match_skill(user_input)
    if skill is None:
        return {"matched": None, "message": "No matching skill found"}
    return {"matched": skill}


@router.get("/validate/all")
async def validate_all_skills_endpoint():
    """Validate all skills and return issues."""
    results = validate_all_skills()
    all_valid = all(len(issues) == 0 for issues in results.values())
    return {
        "valid": all_valid,
        "skills": {
            name: {"valid": len(issues) == 0, "issues": issues}
            for name, issues in results.items()
        },
    }
