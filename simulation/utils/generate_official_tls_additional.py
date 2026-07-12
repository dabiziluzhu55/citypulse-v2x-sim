#!/usr/bin/env python

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def indent(elem, level=0):
    spaces = "\n" + level * "  "
    child_spaces = "\n" + (level + 1) * "  "

    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = child_spaces
        for child in elem:
            indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = spaces
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = spaces


def load_tls_state_lengths(net_xml):
    max_link_index = {}

    for _, elem in ET.iterparse(net_xml, events=("end",)):
        if elem.tag != "connection":
            elem.clear()
            continue

        tl_id = elem.get("tl")
        link_index = elem.get("linkIndex")

        if tl_id is not None and link_index is not None:
            index = int(link_index)
            max_link_index[tl_id] = max(max_link_index.get(tl_id, -1), index)

        elem.clear()

    return {
        tl_id: max_index + 1
        for tl_id, max_index in max_link_index.items()
    }


def make_state(length, indices, value):
    state = ["r"] * length
    for index in indices:
        if index < 0 or index >= length:
            raise ValueError(
                f"linkIndex {index} is outside state length {length}."
            )
        state[index] = value
    return "".join(state)


def validate_program(intersection_id, program):
    phases = program["phases"]
    cycle_duration = int(program["cycle_duration"])

    total = 0
    for phase in phases:
        phase_total = int(phase["total"])
        computed_total = (
            int(phase["green"])
            + int(phase["yellow"])
            + int(phase["all_red"])
        )

        if computed_total != phase_total:
            raise ValueError(
                f"{intersection_id} {program['program_id']} phase "
                f"{phase['official_phase_no']} total mismatch: "
                f"green + yellow + all_red = {computed_total}, total = {phase_total}"
            )

        total += phase_total

    if total != cycle_duration:
        raise ValueError(
            f"{intersection_id} {program['program_id']} cycle mismatch: "
            f"sum(phases) = {total}, cycle_duration = {cycle_duration}"
        )


def append_phase(parent, duration, state, name):
    duration = int(duration)
    if duration <= 0:
        return

    ET.SubElement(
        parent,
        "phase",
        {
            "duration": str(duration),
            "state": state,
            "name": name,
        },
    )


def build_tl_logic(intersection_id, config, program, state_length):
    validate_program(intersection_id, program)

    tls_id = str(config["tls_id"])
    program_id = str(program["program_id"])
    offset = str(program.get("offset", 0))

    tl_logic = ET.Element(
        "tlLogic",
        {
            "id": tls_id,
            "type": "static",
            "programID": program_id,
            "offset": offset,
        },
    )

    mapping = {
        int(item["official_phase_no"]): item
        for item in config["phase_mapping"]
    }

    for phase in program["phases"]:
        phase_no = int(phase["official_phase_no"])

        if phase_no not in mapping:
            raise ValueError(
                f"{intersection_id} {program_id} phase {phase_no} "
                "has no phase_mapping entry."
            )

        phase_mapping = mapping[phase_no]
        link_indices = [
            int(item)
            for item in phase_mapping.get("sumo_link_indices", [])
        ]

        if not link_indices:
            raise ValueError(
                f"{intersection_id} phase {phase_no} has empty sumo_link_indices."
            )

        phase_name = phase.get(
            "official_phase_name",
            phase_mapping.get("official_phase_name", f"phase_{phase_no}"),
        )

        green_char = phase_mapping.get("green_state", "G")

        append_phase(
            tl_logic,
            phase["green"],
            make_state(state_length, link_indices, green_char),
            f"{phase_name}_green",
        )
        append_phase(
            tl_logic,
            phase["yellow"],
            make_state(state_length, link_indices, "y"),
            f"{phase_name}_yellow",
        )
        append_phase(
            tl_logic,
            phase["all_red"],
            "r" * state_length,
            f"{phase_name}_all_red",
        )

    return tl_logic


def generate(plans_json, net_xml, output_xml):
    plans = json.loads(Path(plans_json).read_text(encoding="utf-8"))
    tls_state_lengths = load_tls_state_lengths(net_xml)

    root = ET.Element("additional")

    seen_programs = set()

    for intersection_id, config in plans["intersections"].items():
        tls_id = str(config["tls_id"])

        if tls_id.startswith("TODO"):
            raise ValueError(f"{intersection_id} has unresolved tls_id: {tls_id}")

        if tls_id not in tls_state_lengths:
            raise ValueError(
                f"{intersection_id} tls_id={tls_id} not found in net connections."
            )

        state_length = tls_state_lengths[tls_id]

        for program in config["programs"]:
            program_id = str(program["program_id"])
            key = (tls_id, program_id)

            if key in seen_programs:
                raise ValueError(
                    f"Duplicated tlLogic id/programID: tls_id={tls_id}, "
                    f"programID={program_id}."
                )

            seen_programs.add(key)

            root.append(
                build_tl_logic(
                    intersection_id,
                    config,
                    program,
                    state_length,
                )
            )

    indent(root)

    tree = ET.ElementTree(root)
    output_path = Path(output_xml)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    print(f"Saved official tls additional file to {output_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--plans",
        default="data/maps/sumo/official_tls_plans.json",
        help="official tls plans json",
    )
    parser.add_argument(
        "--net",
        default="data/maps/sumo/TotalMap_20.net.xml",
        help="SUMO net.xml path",
    )
    parser.add_argument(
        "--output",
        default="data/maps/sumo/TotalMap_20_official_tls.add.xml",
        help="output SUMO additional xml",
    )
    args = parser.parse_args()

    generate(args.plans, args.net, args.output)


if __name__ == "__main__":
    main()