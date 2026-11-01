"""A hero of its own: the alias table, the renamed entity family, the herodef.

The chain (docs/_re/kinds/heroes.md): herodef -> skin entity -> AliasPicker GUID
-> ApplicationSettings.ot alias -> gameplay entity. A clone that misses one
link still loads and quietly plays as its base, so each link is asserted.
"""

from __future__ import annotations

import re

import pytest

from rsmm.engine import corpus
from rsmm.engine import entity_strings as ES
from rsmm.engine import hero_cook as H

_APP = """SingleObject80=C43
{
m_pSettings=MAIN
// class oe::Vector<class AliasDesc>
u|Vector.Length=2
Vector[0]=C41
{
u|m_u32Val1=1
u|m_u32Val2=2
u|m_u32Val3=3
u|m_u32Val4=4
s|m_sName=Hero_Common
s|m_sArchive=EntitySettings
s|m_sStream=Heroes\\\\Hero_Common\\\\Hero_Common.entity.ot
}
Vector[1]=C41
{
u|m_u32Val1=404451935
u|m_u32Val2=1133478234
u|m_u32Val3=3553325461
u|m_u32Val4=2481263668
s|m_sName=Hero_Piper
s|m_sArchive=EntitySettings
s|m_sStream=Heroes\\\\Hero_Piper\\\\Hero_Piper.entity.ot
}
}
Object=C4
"""


def _alias(name: str) -> H.Alias:
    return H.Alias(H.alias_guid("m", name), f"Hero_{name}",
                   f"Heroes\\Hero_Piper\\Hero_{name}.entity.ot")


def test_the_alias_table_reads_the_guid_the_skins_carry():
    a = H.aliases(_APP)
    assert [x.name for x in a] == ["Hero_Common", "Hero_Piper"]
    # Piper's skins carry 5f721b18...: the four u32s, little-endian.
    assert a[1].guid.hex() == "5f721b185a818f439571cbd33414e593"
    assert a[1].stream == "Heroes\\Hero_Piper\\Hero_Piper.entity.ot"


def test_an_alias_appends_once_and_bumps_the_length():
    out = H.add_alias(_APP, _alias("Nyx"))
    assert "u|Vector.Length=3" in out and H.aliases(out)[-1] == _alias("Nyx")
    assert "Vector[2]=C41" in out and out.endswith("}\n}\nObject=C4\n")
    assert H.add_alias(out, _alias("Nyx")) == out
    assert "s|m_sStream=Heroes\\\\Hero_Piper\\\\Hero_Nyx.entity.ot" in out


def test_merging_keeps_every_mods_alias_and_the_non_alias_edits():
    edited = _APP.replace("m_pSettings=MAIN", "m_pSettings=EDITED")   # an `ot` patch
    merged = H.merge_app_settings([H.add_alias(_APP, _alias("Nyx")), edited,
                                   H.add_alias(_APP, _alias("Zed"))])
    assert "m_pSettings=EDITED" in merged
    assert [a.name for a in H.aliases(merged)][-2:] == ["Hero_Nyx", "Hero_Zed"]
    assert "u|Vector.Length=4" in merged


def _base() -> H.HeroBase:
    return H.HeroBase(
        "Piper", "Hero_Piper", "Hero_Piper", H.aliases(_APP)[1],
        "Heroes\\Hero_Piper\\Hero_Piper_Default.entity.ot", "Hero_Piper_Common~GAM.xls",
        family=["Heroes\\Hero_Piper\\Hero_Piper.entity.ot",
                "Heroes\\Hero_Piper\\Hero_Piper_Projectile.entity.ot",
                "Heroes\\Hero_Piper\\Hero_Piper_Default.entity.ot"])


@pytest.mark.parametrize("before,after", [
    # A pet reads its owner by template path and scope: both must follow.
    ("Heroes\\Hero_Piper\\Hero_Piper.entity.ot", "Heroes\\Hero_Piper\\Hero_Nyx.entity.ot"),
    ("[Value] Hero_Piper\\Ability Basic\\Basic Attack Chrono Value",
     "[Value] Hero_Nyx\\Ability Basic\\Basic Attack Chrono Value"),
    ("[Cpnt Value Tester] Hero_Piper_Projectile\\Is Basic Attack Condition",
     "[Cpnt Value Tester] Hero_Nyx_Projectile\\Is Basic Attack Condition"),
    ("Heroes\\Hero_Piper\\Hero_Piper_Default.entity.ot",
     "Heroes\\Hero_Piper\\Hero_Nyx_Default.entity.ot"),
    # Not family members: a text bank, a VFX folder, a shared entity.
    ("Hero_Piper_Common~GAM.xls", "Hero_Piper_Common~GAM.xls"),
    ("Settings\\Heroes\\Hero_Piper_FX\\Piper_Note_Day_01.vfx.ot",
     "Settings\\Heroes\\Hero_Piper_FX\\Piper_Note_Day_01.vfx.ot"),
    ("[Value] Hero_Common\\Base\\AP Trait Value", "[Value] Hero_Common\\Base\\AP Trait Value"),
    ("[Value] Hero_Piper_Common\\X", "[Value] Hero_Piper_Common\\X"),
])
def test_the_renamer_moves_family_paths_and_scopes_only(before, after):
    assert H.Renamer(_base(), "Hero_Nyx").string(before) == after


@pytest.mark.skipif(corpus.read(H.entity_rel(
    "Heroes\\Hero_Piper\\Hero_Piper_Projectile.entity.ot")) is None, reason="no hero corpus")
def test_a_renamed_family_member_keeps_no_reference_to_the_base():
    raw = corpus.read(H.entity_rel("Heroes\\Hero_Piper\\Hero_Piper_Projectile.entity.ot"))
    b = _base()
    b.family = [f"Heroes\\Hero_Piper\\{s}.entity.ot" for s in
                (r.rsplit("/", 1)[-1][:-len(H.ENTITY_SUFFIX)] for r in corpus.rels(
                    "EntitySettings/Heroes/Hero_Piper/", H.ENTITY_SUFFIX))]
    out = H.Renamer(b, "Hero_Nyx").cooked(raw)
    left = [t for _s, _o, t in ES.list_strings(out)
            if re.search(r"Heroes\\Hero_Piper\\Hero_Piper|\] Hero_Piper[\\_]", t)]
    assert left == []
    assert any("Hero_Nyx" in t for _s, _o, t in ES.list_strings(out))


@pytest.mark.skipif(corpus.read(H.entity_rel(
    "Heroes\\Hero_Piper\\Hero_Piper_Projectile.entity.ot")) is None, reason="no hero corpus")
def test_values_set_by_label_or_by_member_and_refuse_typos():
    from rsmm.engine.talent_values import list_talent_values
    files = {n: corpus.read(H.entity_rel(f"Heroes\\Hero_Piper\\{n}.entity.ot"))
             for n in ("Hero_Piper", "Hero_Piper_Projectile", "Hero_Piper_Ghost_Note_Projectile")}
    out = H.set_values(files, {"Ultimate Power 2 Cooldown Max": 5,
                               "Hero_Piper_Projectile/Lifetime Duration": 2.0})

    def val(name, label):
        return next(t.value for t in list_talent_values(out[name], include_spawner=True)
                    if t.label == label)
    assert val("Hero_Piper", "Ultimate Power 2 Cooldown Max") == 5
    assert val("Hero_Piper_Projectile", "Lifetime Duration") == 2.0
    assert val("Hero_Piper_Ghost_Note_Projectile", "Lifetime Duration") == 0.5  # untouched
    assert all(len(out[n]) == len(files[n]) for n in files)                      # in place
    with pytest.raises(H.HeroCookError, match="Ultimate Cooldwn"):
        H.set_values(files, {"Ultimate Cooldwn": 1})


@pytest.mark.skipif(corpus.read(H.herodef_rel("Red_Update6")) is None, reason="no hero corpus")
def test_red_family_includes_and_renames_life_bar_and_combo_point_ui():
    from rsmm.engine import paths
    app_text = H.pristine_app_settings(paths.default_game_dir())
    b = H.resolve_base("Red_Update6", app_text)
    assert "Heroes\\Hero_Red\\Red_Combo_Point_Ui.entity.ot" in b.family
    assert "GameUis\\Common_Ui\\Life_Bar_Hero_Red.entity.ot" in b.family

    r = H.Renamer(b, "Hero_BloodWolf")
    assert r.entity_ref("GameUis\\Common_Ui\\Life_Bar_Hero_Red.entity.ot") == \
        "GameUis\\Common_Ui\\Life_Bar_Hero_BloodWolf.entity.ot"
    assert r.entity_ref("Heroes\\Hero_Red\\Red_Combo_Point_Ui.entity.ot") == \
        "Heroes\\Hero_Red\\BloodWolf_Combo_Point_Ui.entity.ot"
    assert r.string("[Value] Hero_Red\\Ability Common\\Combo Points Current Value") == \
        "[Value] Hero_BloodWolf\\Ability Common\\Combo Points Current Value"
    assert r.string("[Game Ui] Red_Combo_Point_Ui\\Game Ui") == \
        "[Game Ui] BloodWolf_Combo_Point_Ui\\Game Ui"
    assert r.string("[Entity Get Value] Life_Bar_Hero_Red\\Getter\\Combo Counter Max Get Value") == \
        "[Entity Get Value] Life_Bar_Hero_BloodWolf\\Getter\\Combo Counter Max Get Value"
