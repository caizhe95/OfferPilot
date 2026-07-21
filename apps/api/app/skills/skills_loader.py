"""Skills loader and validator.

Loads Skills from the harness/skills directory, validates them against
skill-creator specification, and provides intent matching.
"""

import re
from pathlib import Path
from typing import Any

from app.llm.llm_client import structured_json_completion


def _find_skills_dir() -> Path:
    """Find the harness/skills directory relative to this file."""
    current = Path(__file__).resolve().parent
    # Go up from apps/api/app/skills -> lite-offerpilot root
    root = current.parent.parent.parent.parent
    skills_dir = root / "harness" / "skills"
    return skills_dir


def _read_yaml_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML frontmatter from Markdown content.

    Returns (frontmatter_dict, body_content).
    """
    frontmatter = {}
    body = content

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if match:
        try:
            # Use PyYAML directly since we need to parse frontmatter
            import yaml as yaml_lib
            frontmatter = yaml_lib.safe_load(match.group(1)) or {}
        except Exception:
            frontmatter = {}
        body = content[match.end():]

    return frontmatter, body


def validate_skill(skill_dir: Path) -> list[str]:
    """Validate a Skill directory against skill-creator specification.

    Returns list of warning/error messages. Empty list means valid.
    """
    issues = []

    # Check folder name is hyphen-case
    folder_name = skill_dir.name
    if not re.match(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", folder_name):
        issues.append(f"Skill folder name '{folder_name}' is not valid hyphen-case")

    # Check SKILL.md exists
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        issues.append("Missing SKILL.md")
        return issues

    content = skill_md.read_text(encoding="utf-8")
    frontmatter, body = _read_yaml_frontmatter(content)

    # Frontmatter must contain only 'name' and 'description'
    allowed_keys = {"name", "description"}
    extra_keys = set(frontmatter.keys()) - allowed_keys
    if extra_keys:
        issues.append(f"Frontmatter contains extra fields: {extra_keys}")

    # name must be present and valid
    if "name" not in frontmatter:
        issues.append("Frontmatter missing 'name'")
    else:
        name = frontmatter["name"]
        if not re.match(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", name):
            issues.append(f"Skill name '{name}' is not valid hyphen-case")
        if name != folder_name:
            issues.append(f"Skill name '{name}' does not match folder name '{folder_name}'")

    # description must exist and contain trigger semantics
    if "description" not in frontmatter:
        issues.append("Frontmatter missing 'description'")
    else:
        desc = frontmatter["description"]
        if len(desc) < 20:
            issues.append("Description is too short (min 20 chars)")
        # Check for trigger semantics - should mention when to use
        trigger_keywords = ["when", "use this", "trigger", "provide", "ask", "upload"]
        has_trigger = any(kw in desc.lower() for kw in trigger_keywords)
        if not has_trigger:
            issues.append("Description lacks trigger semantics")

    # Body must be non-empty
    if not body.strip():
        issues.append("SKILL.md body is empty")

    # SKILL.md should not exceed 500 lines
    line_count = content.count("\n") + 1
    if line_count > 500:
        issues.append(f"SKILL.md exceeds 500 lines ({line_count} lines)")

    # Check for disallowed files
    disallowed = {"README", "README.md", "CHANGELOG", "CHANGELOG.md",
                  "INSTALLATION_GUIDE", "INSTALLATION_GUIDE.md"}
    for f in skill_dir.iterdir():
        if f.name in disallowed:
            issues.append(f"Contains disallowed file: {f.name}")

    # Check references exist (if referenced in body)
    ref_pattern = re.findall(r"`references/([^`]+)`", body)
    ref_dir = skill_dir / "references"
    for ref in ref_pattern:
        ref_path = ref_dir / ref
        if not ref_path.exists():
            issues.append(f"Referenced file not found: references/{ref}")

    return issues


def validate_all_skills(skills_dir: Path | None = None) -> dict[str, list[str]]:
    """Validate all skills and return {skill_name: issues}."""
    if skills_dir is None:
        skills_dir = _find_skills_dir()

    results = {}
    if not skills_dir.exists():
        return results

    for skill_dir in sorted(skills_dir.iterdir()):
        if skill_dir.is_dir() and not skill_dir.name.startswith("."):
            issues = validate_skill(skill_dir)
            results[skill_dir.name] = issues

    return results


def load_skill(skill_name: str, skills_dir: Path | None = None) -> dict | None:
    """Load a Skill's SKILL.md content and metadata.

    Returns dict with name, description, body, references, or None if not found.
    """
    if skills_dir is None:
        skills_dir = _find_skills_dir()

    skill_dir = skills_dir / skill_name
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.exists():
        return None

    content = skill_md.read_text(encoding="utf-8")
    frontmatter, body = _read_yaml_frontmatter(content)

    return {
        "name": frontmatter.get("name", skill_name),
        "description": frontmatter.get("description", ""),
        "body": body.strip(),
        "path": str(skill_dir),
    }


def load_skill_references(skill_name: str, skills_dir: Path | None = None) -> list[dict]:
    """Load referenced files for a skill.

    Returns list of {name, content} for each reference file.
    """
    if skills_dir is None:
        skills_dir = _find_skills_dir()

    skill_dir = skills_dir / skill_name
    ref_dir = skill_dir / "references"

    if not ref_dir.exists():
        return []

    refs = []
    for ref_file in sorted(ref_dir.iterdir()):
        if ref_file.suffix == ".md":
            refs.append({
                "name": ref_file.name,
                "content": ref_file.read_text(encoding="utf-8"),
            })

    return refs


def list_skills(skills_dir: Path | None = None) -> list[dict]:
    """List all available skills with their metadata."""
    if skills_dir is None:
        skills_dir = _find_skills_dir()

    skills = []
    if not skills_dir.exists():
        return skills

    for skill_dir in sorted(skills_dir.iterdir()):
        if skill_dir.is_dir() and not skill_dir.name.startswith("."):
            skill = load_skill(skill_dir.name, skills_dir)
            if skill:
                skills.append(skill)

    return skills


_SKILL_TRIGGERS = {
    "interview-diagnosis": [
        "面试", "诊断", "评分", "question",
        "answer", "diagnos", "score", "assess",
        "什么是", "对吗", "怎么样回答", "这样回答",
    ],
    "answer-rewrite": [
        "优化", "改写", "重写", "rewrite", "optimize",
        "improve", "修改", "润色",
    ],
    "followup-coaching": [
        "追问", "followup", "面试官", "可能会问", "下一个问题",
        "面试策略", "coaching", "回答策略",
    ],
    "audio-diagnosis": [
        "音频", "录音", "audio", "transcript", "转写",
        "语音", "上传", "文本", "转写文本",
    ],
}


def _looks_domain_related(user_input: str) -> bool:
    input_lower = user_input.lower()
    related_terms = {
        "面试", "回答", "诊断", "评分", "追问", "优化", "改写", "润色",
        "录音", "音频", "转写", "语音", "候选", "求职", "offer",
        "interview", "answer", "question", "rewrite", "optimize",
        "followup", "coaching", "audio", "transcript", "agent",
    }
    return any(term in input_lower for term in related_terms)


def _attach_selection_metadata(skill: dict | None, source: str, reason: str = "") -> dict | None:
    if skill is None:
        return None
    selected = dict(skill)
    selected["selection_source"] = source
    if reason:
        selected["selection_reason"] = reason
    return selected


def _match_skill_heuristic(user_input: str, skills_dir: Path | None = None) -> dict | None:
    """Fallback skill matcher using the original weighted trigger rules."""
    skills = list_skills(skills_dir)
    if not skills:
        return None

    input_lower = user_input.lower()

    scores = {}
    input_matched = {}
    for skill in skills:
        name = skill["name"]
        triggers = _SKILL_TRIGGERS.get(name, [])
        # Input matches: weight 3
        input_score = sum(3 for t in triggers if t in input_lower)
        # Description matches: only count as tiebreaker
        desc_lower = skill["description"].lower()
        desc_score = sum(0.5 for t in triggers if t in desc_lower)
        scores[name] = input_score + desc_score
        input_matched[name] = input_score > 0

    # Return highest scoring skill that has at least one input match
    if scores:
        # Filter to skills with at least one input match
        matched = {n: s for n, s in scores.items() if input_matched[n]}
        if matched:
            best = max(matched, key=lambda k: matched[k])
            return load_skill(best, skills_dir)

    # Fallback: interview-diagnosis for any interview-related input
    if any(kw in input_lower for kw in ["面试", "interview", "诊断", "question"]):
        return load_skill("interview-diagnosis", skills_dir)

    return None


def _validate_skill_selection(data: dict[str, Any], skill_names: set[str]) -> bool:
    if "skill_name" not in data:
        return False
    skill_name = data.get("skill_name")
    if skill_name is None:
        return True
    return isinstance(skill_name, str) and skill_name in skill_names


def match_skill(user_input: str, skills_dir: Path | None = None) -> dict | None:
    """Match user input to the most appropriate Skill.

    Uses the LLM as the primary semantic router and keeps the original
    weighted keyword matcher as the deterministic fallback.
    """
    skills = list_skills(skills_dir)
    if not skills:
        return None
    if not _looks_domain_related(user_input):
        return None

    skill_names = {s["name"] for s in skills}

    def fallback() -> dict[str, Any]:
        skill = _match_skill_heuristic(user_input, skills_dir)
        return {
            "skill_name": skill["name"] if skill else None,
            "reason": "heuristic fallback",
        }

    system_prompt = (
        "你是 OfferPilot Lite 的 Skill Router。"
        "根据用户输入，从候选 skills 中选择最适合的一个。"
        "如果输入明显与面试回答诊断、回答改写、追问辅导或音频转写诊断无关，skill_name 必须为 null。"
        "只输出 JSON：{\"skill_name\": string|null, \"reason\": string}。"
    )
    payload = {
        "input": user_input,
        "skills": [
            {"name": skill["name"], "description": skill.get("description", "")}
            for skill in skills
        ],
    }

    result = structured_json_completion(
        task_name="skill_selection",
        system_prompt=system_prompt,
        user_payload=payload,
        fallback_factory=fallback,
        validator=lambda data: _validate_skill_selection(data, skill_names),
        temperature=0.0,
    )
    skill_name = result.data.get("skill_name")
    if not skill_name:
        return None

    selected = load_skill(str(skill_name), skills_dir)
    return _attach_selection_metadata(
        selected,
        source=result.source,
        reason=str(result.data.get("reason", "")).strip(),
    )
