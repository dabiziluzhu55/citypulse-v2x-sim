#!/usr/bin/env python3
"""
bin2pcd.py — KITTI velodyne .bin 点云 → PCD 格式转换

KITTI `.bin`(x/y/z/intensity 小端 float32,每点 16 字节)与 PCL PCD
binary 模式的原始数据布局完全一致,因此转换 = PCD 文本头部 + **原字节
零拷贝直写**,无损且秒级。输出可直接用 CloudCompare / Open3D / 在线
PCD 查看器打开。

用法:
    python bin2pcd.py <input.bin> [output.pcd]     # 单文件(缺省同名 .pcd)
    python bin2pcd.py <kitti_dir> [out_dir]        # 目录批量:velodyne_*/*.bin 全转
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

POINT_BYTES = 16  # x, y, z, intensity — 4 × float32


def _pcd_header(n: int) -> str:
    """PCL PCD v0.7 binary 头部(字段布局与 .bin 原始数据一致)。"""
    return (
        "# .PCD v0.7 - Point Cloud Data file format\n"
        "VERSION 0.7\n"
        "FIELDS x y z intensity\n"
        "SIZE 4 4 4 4\n"
        "TYPE F F F F\n"
        "COUNT 1 1 1 1\n"
        f"WIDTH {n}\n"
        "HEIGHT 1\n"
        "VIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {n}\n"
        "DATA binary\n"
    )


def bin_to_pcd(bin_path: str, pcd_path: str) -> int:
    """把单个 KITTI .bin 点云写为 PCD binary(零拷贝直写数据块)。

    Raises:
        ValueError: 文件大小不是 16 的倍数(损坏/非点云文件)。
        OSError: 读写失败。
    """
    with open(bin_path, "rb") as fh:
        raw = fh.read()
    if len(raw) % POINT_BYTES != 0:
        raise ValueError(
            f"{bin_path}: size {len(raw)} is not a multiple of "
            f"{POINT_BYTES} bytes (x/y/z/intensity f32) — not a velodyne bin")
    n = len(raw) // POINT_BYTES
    parent = os.path.dirname(pcd_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(pcd_path, "wb") as fh:
        fh.write(_pcd_header(n).encode("ascii"))
        fh.write(raw)  # 原字节直写:布局与 FIELDS/SIZE/TYPE 声明完全一致
    return n


def convert_tree(root: str, out_root: Optional[str] = None) -> int:
    """批量转换 ``<root>/velodyne_*/*.bin`` 为 .pcd。

    ``out_root`` 缺省时在原目录生成同名 .pcd;指定时保持 velodyne_*/ 子目录
    结构。返回转换的文件数。
    """
    bins = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath).startswith("velodyne_"):
            bins.extend(os.path.join(dirpath, f)
                        for f in filenames if f.endswith(".bin"))
    if not bins:
        print(f"no velodyne_*/*.bin found under {root}")
        return 0
    converted = 0
    for bin_path in sorted(bins):
        if out_root:
            rel = os.path.relpath(bin_path, root)
            pcd_path = os.path.join(out_root, rel)[:-4] + ".pcd"
        else:
            pcd_path = bin_path[:-4] + ".pcd"
        n = bin_to_pcd(bin_path, pcd_path)
        converted += 1
        print(f"  {bin_path} ({n} points) → {pcd_path}")
    return converted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="KITTI velodyne .bin 点云 → PCD(在线查看用)")
    parser.add_argument("path", help="单个 .bin 文件,或包含 velodyne_*/ 的 kitti 目录")
    parser.add_argument("out", nargs="?", default=None,
                        help="输出 .pcd 路径(单文件)或输出根目录(批量);缺省原位生成")
    args = parser.parse_args(argv)

    if os.path.isdir(args.path):
        n = convert_tree(args.path, args.out)
        print(f"{n} file(s) converted")
        return 0
    if not os.path.isfile(args.path):
        parser.error(f"not a file or directory: {args.path}")

    out = args.out or args.path[:-4] + ".pcd"
    n = bin_to_pcd(args.path, out)
    print(f"{args.path} ({n} points) → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
