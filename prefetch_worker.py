#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PVE 数据预拉脚本 -- 每 5 分钟由 cron 触发,
把 hosts 列表和每个 host 的 VM 详情写入 SQLite cache_entries,
让 Web 页面打开时直接读本地库,秒开体验。
"""
import sys, sqlite3, json, time, logging, os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from app import (
    DB_PATH,
    summarize_host_row,
    fetch_host_vms,
    set_sqlite_cache,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("prefetch")


def main():
    logger.info("=== prefetch started ===")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM hosts ORDER BY id").fetchall()
        conn.close()
    except Exception as e:
        logger.error("读取 hosts 表失败: %s", e)
        return

    if not rows:
        logger.info("hosts 表为空,无需预拉")
        return

    # 预热 hosts_summary
    try:
        results = []
        for row in rows:
            logger.info("summarizing host %s %s ...", row["id"], row["name"])
            info = summarize_host_row(row)
            results.append(info)
        set_sqlite_cache("hosts_summary", results)
        logger.info("hosts_summary cached, %d hosts", len(results))
    except Exception as e:
        logger.error("hosts_summary 预热失败: %s", e)

    # 预热每个 host 的 VM
    for row in rows:
        host_id = row["id"]
        host_name = row["name"]
        try:
            logger.info("fetching VMs for host %s %s ...", host_id, host_name)
            vms, error = fetch_host_vms(row, include_agent=True)
            if error:
                logger.warning("host %s VM fetch error: %s", host_id, error)
                continue
            set_sqlite_cache("host_vms:{}".format(host_id), vms)
            logger.info("host %s VM cached, %d VMs", host_id, len(vms))
        except Exception as e:
            logger.error("host %s VM prefetch error: %s", host_id, e)

    logger.info("=== prefetch finished ===")


if __name__ == "__main__":
    main()
