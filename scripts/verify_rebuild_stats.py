#!/usr/bin/env python3
import os
import sqlite3


def main():
    db_path = ".state/public-glintory.sqlite3"
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("=== Database Verification ===")
    
    # 1. Total Opportunities count by status
    print("\n[Opportunities Count by Status]")
    cursor.execute("SELECT status, COUNT(*) FROM opportunities GROUP BY status")
    for status, count in cursor.fetchall():
        print(f"  {status}: {count}")

    # 2. Total Opportunities count by gate_status
    print("\n[Opportunities Count by Gate Status]")
    cursor.execute("SELECT gate_status, COUNT(*) FROM opportunities GROUP BY gate_status")
    for gate, count in cursor.fetchall():
        print(f"  {gate}: {count}")

    # 3. Gate version counts
    print("\n[Opportunities Count by Gate Version]")
    cursor.execute("SELECT gate_version, COUNT(*) FROM opportunities GROUP BY gate_version")
    for version, count in cursor.fetchall():
        print(f"  {version or 'NULL'}: {count}")

    # 4. Clustering version counts
    print("\n[Opportunities Count by Cluster Version]")
    cursor.execute("SELECT cluster_version, COUNT(*) FROM opportunities GROUP BY cluster_version")
    for version, count in cursor.fetchall():
        print(f"  {version or 'NULL'}: {count}")

    # 5. Top 10 Opportunities
    print("\n[Top 10 Opportunities by Score]")
    cursor.execute("""
        SELECT public_id, title, total_score, status, gate_status, gate_reason
        FROM opportunities
        WHERE current_scoring_version = 'v2'
        ORDER BY total_score DESC, id ASC
        LIMIT 10
    """)
    rows = cursor.fetchall()
    print(f"{'Public ID':<35} | {'Title':<45} | {'Score':<5} | {'Status':<10} | {'Gate':<8}")
    print("-" * 115)
    for pub_id, title, score, status, gate, reason in rows:
        title_truncated = title[:45] if title else "None"
        gate_str = str(gate) if gate is not None else "None"
        score_str = str(score) if score is not None else "None"
        print(f"{pub_id:<35} | {title_truncated:<45} | {score_str:<5} | {status:<10} | {gate_str:<8}")
        print(f"    Reason: {reason}")
        print("-" * 115)

    conn.close()

if __name__ == "__main__":
    main()
