"""Ability edits declared in a manifest, applied to a hero's entity family.

A custom hero (``kind = "hero"``) lists its edits as ``[[content.abilities]]``
steps, applied in order to the BASE hero's entities, by the part names
``rsmm entity-graph <Base>`` shows. The hero kind then renames the family, so
the edits land in the hero's own entities and no other hero is touched.

One step per table::

    clone = "Ability Secondary"           a group, or a list of parts
    as = "Echo"                           new group name (required for a group)
    suffix = " Echo"                      appended to every copied part's name
                                          (default: " " + as)
    from = "Juliet"                       copy from another hero's entity, or
                                          any entity by its path under
                                          EntitySettings ("Objects/Magical_Objects/
                                          Common/Dash_Crit_Chance")

    set = "Primary Ability Shots Delay.value"
    value = 0.2                           a number, bool or [x, y, z]; a number
                                          linked to another part becomes this
                                          literal (the link is dropped)

    override = "Initialize In Default Form Value"   bring a part of an ANCESTOR
    from = "Heroes/Hero_Common/Hero_Alt_Form_Common"  entity in as a record that
                                          replaces it in the merged hero, so it
                                          can be edited (see entity_graph_edit).
                                          ``from`` is a hero name, or any entity
                                          by its path under EntitySettings. This
                                          is the only way to change a part the
                                          hero does not already override: a
                                          ``clone`` would add a second part the
                                          engine also instantiates.

    set = "My Spawner.position[8]"        the n-th number inside an object
    value = 1.5708                        field (the spawn's yaw, radians)

    link = "Primary Ability Shoot Timer.on_end"
    to = "State Secondary Ability"        "" points it at nothing

    add_link = "State Secondary Ability.activates"
    to = "Secondary Ability Clip Echo"

    remove_link = "State Secondary Ability.activates[0]"

    remove_link = "On Damage Received.activates"    the same, by what it points at:
    to = "Modifier"                       steady when other steps change the order

    add_subobject = "Skill Attack Damage.bytes_36"   attach a sub-object a donor
    donor = "Skill Defense Aoe Bleed Damage.bytes_36[0]"  entity owns in one of
    from = "Carmilla"                     its id lists (a damage's debuffs, an
                                          Fx's setups); ``donor_index = n`` picks
                                          one of THIS entity's instead
    replace_ref = { "Old Part" = "New Part" }   re-point the copy's references

    replace_subobject = "Fx_Explosion.bytes_1[0]"    the same, but in place of
    from = "Heroes/Hero_Carmilla/Hero_Carmilla_Power_Orb"  the sub-object the
    donor = "Explosion Fx Active.bytes_1[0]"      slot already holds: the slot
                                          keeps its id, so nothing that names it
                                          changes (an Fx list plays one setup per
                                          slot; adding would play both)

    percent = "Descripton Format.obj_4"   a card slot shown x100 as a percentage
    value = true                          (false: the plain number)

``entity = "FX"`` (or a full stem) on any step targets another member of the
family, e.g. ``Hero_Piper_FX``; the default is the gameplay entity.

After the last step every edited entity goes through ``entity_check``: an
error (a link into an entity this hero does not carry, a sub-object with no
owner, ...) stops the apply rather than install an ability that does nothing.
"""

from __future__ import annotations

import struct

from dataclasses import dataclass, field

from . import entity_check as CK
from .entity_graph_edit import EntityEditError, EntityFile

_OPS = ("clone", "override", "set", "link", "add_link", "remove_link", "percent",
        "add_subobject", "replace_subobject")


class AbilityEditError(ValueError):
    pass


@dataclass
class Result:
    files: dict[str, bytes]                     # stem -> edited bytes
    resources: set[str] = field(default_factory=set)   # new resources to preload
    warnings: list[str] = field(default_factory=list)


def source_entity_ref(name: str) -> str:
    """A clone's ``from``: a hero name, or an entity path under EntitySettings."""
    if "/" not in name and "\\" not in name:
        return hero_entity_ref(name)
    p = name.replace("/", "\\").strip("\\")
    p = p.removeprefix("EntitySettings\\")
    return p if p.endswith(".entity.ot") else p.removesuffix(".entity") + ".entity.ot"


def hero_entity_ref(name: str) -> str:
    """``Juliet`` / ``Hero_Juliet`` -> ``Heroes\\Hero_Juliet\\Hero_Juliet.entity.ot``."""
    stem = name if name.startswith("Hero_") else f"Hero_{name}"
    return f"Heroes\\{stem}\\{stem}.entity.ot"


def _subobject_source(step: dict, ef: EntityFile, c, f, read):
    """The donor sub-object an ``add_subobject`` / ``replace_subobject`` step
    names, and the donor GUIDs it re-points at components of this file."""
    from . import entity_fields as EF
    donor_src = ef
    if step.get("from"):
        ref = source_entity_ref(str(step["from"]))
        raw_donor = read(ref)
        if raw_donor is None:
            raise AbilityEditError(f"no entity {ref}")
        donor_src = EntityFile(raw_donor, ref.rsplit("\\", 1)[-1][:-len(".entity.ot")])
    donor_spec = step.get("donor")
    if donor_spec:
        d_part, _, d_fld = donor_spec.rpartition(".")
        d_base, _, d_idx = d_fld.partition("[")
        dc = donor_src.component(d_part)
        df = next((x for x in EF.fields(dc) if x.name == d_base), None)
        if df is None:
            raise AbilityEditError(f"{d_part!r} has no field {d_base!r}")
        dn = struct.unpack_from("<I", dc.body, df.offset)[0]
        d_i = int(d_idx.rstrip("]")) if d_idx else 0
        if d_i < 0 or d_i >= dn:
            raise AbilityEditError(f"donor index {d_i} out of range (have {dn} items)")
        donor_obj_id = struct.unpack_from(f"<{dn}I", dc.body, df.offset + 4)[d_i]
    else:
        n = struct.unpack_from("<I", c.body, f.offset)[0]
        donor_idx = int(step.get("donor_index", 0))
        if donor_idx < 0 or donor_idx >= n:
            raise AbilityEditError(f"donor_index {donor_idx} out of range (have {n} items)")
        donor_obj_id = struct.unpack_from(f"<{n}I", c.body, f.offset + 4)[donor_idx]
    refs_map = {}
    if "replace_ref" in step:
        rep = step["replace_ref"]
        if not isinstance(rep, dict):
            raise AbilityEditError("replace_ref must be a mapping of old_component -> new_component")
        for old_name, new_name in rep.items():
            refs_map[donor_src.component(old_name).guid] = str(new_name)
    return donor_src, donor_obj_id, refs_map


def apply(files: dict[str, bytes], steps: list[dict], *, main: str, seed: str,
          read=None) -> Result:
    """Apply ``steps`` to ``files`` (entity stem -> bytes, the base family)."""
    read = read or CK._corpus_reader
    edits: dict[str, EntityFile] = {}

    def member(step: dict) -> EntityFile:
        want = str(step.get("entity") or main)
        stem = want if want in files else f"{main}_{want}"
        if stem not in files:
            raise AbilityEditError(f"no family entity {want!r}; have {', '.join(sorted(files))}")
        if stem not in edits:
            edits[stem] = EntityFile(files[stem], stem)
        return edits[stem]

    for n, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise AbilityEditError(f"ability step {n} is not a table")
        ops = [op for op in _OPS if op in step]
        if len(ops) != 1:
            raise AbilityEditError(f"ability step {n} needs exactly one of {', '.join(_OPS)}")
        op = ops[0]
        try:
            ef = member(step)
            if op == "clone":
                _clone(ef, step, f"{seed}:{n}", read)
            elif op == "override":
                _override(ef, step, f"{seed}:{n}", read)
            else:
                part, _, fld = str(step[op]).rpartition(".")
                if not part:
                    raise AbilityEditError(f"{op} = {step[op]!r}: write it as 'Part.field'")
                if op == "set":
                    if "value" not in step:
                        raise AbilityEditError("set needs a value")
                    ef.set_value(part, fld, step["value"])
                elif op == "link":
                    ef.set_ref(part, fld, str(step.get("to") or "") or None)
                elif op == "add_link":
                    ef.add_ref(part, fld, str(step["to"]))
                elif op == "percent":
                    if not isinstance(step.get("value", True), bool):
                        raise AbilityEditError("percent takes value = true or false")
                    ef.set_format_mode(part, fld, step.get("value", True))
                elif op in ("add_subobject", "replace_subobject"):
                    from . import entity_fields as EF
                    base, slot = fld, None
                    if op == "replace_subobject":
                        base, _, idx = fld.partition("[")
                        if not (idx.endswith("]") and idx[:-1].isdigit()):
                            raise AbilityEditError(
                                f"replace_subobject = {step[op]!r}: name the slot, "
                                f"'Part.list[n]'")
                        slot = int(idx[:-1])
                    c = ef.component(part)
                    f = next((x for x in EF.fields(c) if x.name == base), None)
                    if f is None:
                        raise AbilityEditError(f"{part!r} has no field {base!r}")
                    donor_src, donor_obj_id, refs_map = _subobject_source(step, ef, c, f, read)
                    if op == "replace_subobject":
                        ef.replace_subobject(part, base, donor_src, donor_obj_id,
                                             index=slot, refs=refs_map)
                    else:
                        switch = step.get("switch")
                        ef.add_subobject(part, base, donor_src, donor_obj_id,
                                         refs=refs_map,
                                         switch=str(switch) if switch else None)
                elif op == "remove_link":
                    base, _, idx = fld.partition("[")
                    if idx:
                        ef.remove_ref(part, base, int(idx.rstrip("]")))
                    elif step.get("to"):
                        ef.remove_ref(part, base, _link_index(ef, part, base, str(step["to"])))
                    else:
                        raise AbilityEditError("remove_link names an element: 'Part.list[i]', "
                                               "or 'Part.list' with to = the part it points at")
        except (EntityEditError, KeyError, ValueError) as e:
            raise AbilityEditError(f"ability step {n} ({op}): {e}") from e

    out = Result(dict(files))
    for stem, ef in edits.items():
        edited = ef.to_bytes()
        issues = CK.check(edited, files[stem], name=stem, read=read)
        errs = list(CK.errors(issues))
        if errs:
            raise AbilityEditError(
                f"the ability edits to {stem} do not hold together:\n  "
                + "\n  ".join(map(str, errs)))
        out.warnings += [f"{stem}: {i}" for i in issues
                         if not i.error and "no preload cache" not in i.message]
        out.resources |= CK.resource_paths(edited) - CK.resource_paths(files[stem])
        out.files[stem] = edited
    return out


def _link_index(ef: EntityFile, part: str, field: str, to: str) -> int:
    """The position in ``part``'s reference list ``field`` of the link to ``to``."""
    from . import entity_fields as EF
    f = next((x for x in EF.fields(ef.component(part)) if x.name == field), None)
    if f is None or f.kind != "ref[]":
        raise AbilityEditError(f"{part!r} has no reference list {field!r}")
    hits = [i for i, x in enumerate(f.items)
            if x.text.rstrip().rsplit("\\", 1)[-1].removesuffix('""') == to]
    if len(hits) != 1:
        have = ", ".join(x.text.rstrip().rsplit("\\", 1)[-1] for x in f.items) or "nothing"
        raise AbilityEditError(f"{part}.{field} links to {to!r} {len(hits)} times "
                               f"(it links to: {have})")
    return hits[0]


def _override(ef: EntityFile, step: dict, seed: str, read) -> None:
    """``override = "Part"``: bring the ancestor entity's part in as the record
    that replaces it, so the steps after it edit the real thing."""
    what = str(step["override"])
    if "." in what:
        raise AbilityEditError(f"override = {what!r}: name a whole part, not a field")
    ref = source_entity_ref(str(step.get("from") or ""))
    raw = read(ref)
    if raw is None:
        raise AbilityEditError(f"no entity {ref}")
    src = EntityFile(raw, ref.rsplit("\\", 1)[-1][:-len(".entity.ot")])
    ef.override(what, src, seed=seed)


def _clone(ef: EntityFile, step: dict, seed: str, read) -> None:
    src = ef
    if step.get("from"):
        ref = source_entity_ref(str(step["from"]))
        raw = read(ref)
        if raw is None:
            raise AbilityEditError(f"no entity {ref}")
        src = EntityFile(raw, ref.rsplit("\\", 1)[-1][:-len(".entity.ot")])
    what = step["clone"]
    new_group = str(step.get("as") or "")
    groups = src.graph().groups()
    if isinstance(what, str) and what in groups:
        if not new_group:
            raise AbilityEditError(f"cloning group {what!r} needs 'as' (its new name)")
        names = [c.name for c in groups[what]]
        regroup = {what: new_group}
    else:
        names = [what] if isinstance(what, str) else [str(x) for x in what]
        regroup = {}
    rename = step.get("rename")
    if isinstance(rename, dict):
        rename_map = {str(k): str(v) for k, v in rename.items()}
    else:
        suffix = str(step.get("suffix", f" {new_group}" if new_group else " Copy"))
        rename_map = {x: x + suffix for x in names}
    ef.clone(names, rename=rename_map, group=regroup, seed=seed,
             source=None if src is ef else src)
