import os
from collections import deque

from flask import Flask, render_template, request

import db

app = Flask(__name__)


def lookup_runner_id(cur, name):
    cur.execute("SELECT runner_id FROM runners WHERE name ILIKE %s LIMIT 1", (name.strip(),))
    row = cur.fetchone()
    return row[0] if row else None


def get_neighbors(cur, runner_id, sport_filter):
    """
    Pairwise wins are never materialized: 'runner_id beat other' is derived
    on the fly from same-race placings, so storage stays O(results) instead
    of O(pairs) while every full-pairwise connection is still reachable.
    """
    query = """
        SELECT other.runner_id, m.name, m.date, r.event, r.team, other.team
        FROM results r
        JOIN results other
          ON other.meet_id = r.meet_id
         AND other.event = r.event
         AND other.gender = r.gender
         AND other.place > r.place
        JOIN meets m ON m.meet_id = r.meet_id
        WHERE r.runner_id = %s
    """
    params = [runner_id]
    if sport_filter:
        query += " AND m.sport = %s"
        params.append(sport_filter)
    cur.execute(query, params)
    return cur.fetchall()


def shortest_path(conn, start_name, end_name, sport_filter):
    with conn.cursor() as cur:
        start_id = lookup_runner_id(cur, start_name)
        if start_id is None:
            raise ValueError(f"Runner '{start_name}' not found.")
        end_id = lookup_runner_id(cur, end_name)
        if end_id is None:
            raise ValueError(f"Runner '{end_name}' not found.")

        queue = deque([start_id])
        visited = {start_id: None}
        found = start_id == end_id

        while queue and not found:
            current = queue.popleft()
            for nbr, meet_name, date, event, from_team, to_team in get_neighbors(cur, current, sport_filter):
                if nbr not in visited:
                    visited[nbr] = (current, meet_name, date, event, from_team, to_team)
                    if nbr == end_id:
                        found = True
                        break
                    queue.append(nbr)

        if not found:
            return None, None, {}

        path = []
        cur_id = end_id
        while cur_id is not None:
            path.append(cur_id)
            edge = visited[cur_id]
            cur_id = edge[0] if edge else None
        path.reverse()

        cur.execute("SELECT runner_id, name FROM runners WHERE runner_id = ANY(%s)", (path,))
        names = dict(cur.fetchall())

    return path, visited, names


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        start_athlete = request.form.get("start_athlete", "").strip()
        end_athlete = request.form.get("end_athlete", "").strip()
        sport = request.form.get("sport", "both")
        sport_filter = None if sport == "both" else sport

        if not start_athlete or not end_athlete:
            return render_template("index.html", error="Both fields are required.")

        conn = db.get_conn()
        try:
            path, visited, names = shortest_path(conn, start_athlete, end_athlete, sport_filter)
        except ValueError as e:
            return render_template("index.html", error=str(e))
        finally:
            conn.close()

        if not path:
            error_msg = f"No path found from '{start_athlete}' to '{end_athlete}'."
            return render_template("index.html", error=error_msg)

        path_lines = []
        for i in range(len(path) - 1):
            n1, n2 = path[i], path[i + 1]
            _, meet_name, date, event, from_team, to_team = visited[n2]
            name1 = names.get(n1, "Unknown")
            name2 = names.get(n2, "Unknown")
            path_lines.append(
                f"{name1} ({from_team}) beat {name2} ({to_team}) "
                f"in the {event} at the {meet_name} on {date}"
            )

        return render_template("result.html", start=start_athlete, end=end_athlete, path_lines=path_lines)

    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
