"""Run the Graph Data Science algorithms and write their results into the graph.

Usage (from the project root, with Neo4j running and loaded):
    python -m scripts.graph_algorithms

1. Project an in-memory graph Recipe -[CONTAINS {weight}]-> Ingredient, where weight is the IDF of the
   ingredient: log(number of recipes / recipes using it). Rare ingredients weigh more than salt.
2. Node Similarity (weighted Jaccard) on that projection: for each recipe, its 10 most similar recipes,
   written as (:Recipe)-[:SIMILAR_TO {score}]->(:Recipe).
3. Drop the in-memory projection.
"""

import time

from app.config import settings
from app.database.neo4j_client import Neo4jClient

GRAPH_NAME = "recipe_ingredients"
TOP_K = 10
SIMILARITY_CUTOFF = 0.1
DEGREE_CUTOFF = 3  # recipes with fewer than 3 ingredients are not compared (too little information)

DROP_PROJECTION = "CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName"

PROJECT_WEIGHTED = """
MATCH (r:Recipe) WITH count(r) AS recipes
MATCH (r:Recipe)-[:CONTAINS]->(i:Ingredient)
WITH gds.graph.project(
  $name, r, i,
  {sourceNodeLabels: 'Recipe', targetNodeLabels: 'Ingredient', relationshipType: 'CONTAINS',
   relationshipProperties: {weight: log(toFloat(recipes) / i.recipe_count)}}
) AS graph
RETURN graph.nodeCount AS nodes, graph.relationshipCount AS relationships
"""

DELETE_OLD_SIMILARITIES = "MATCH ()-[s:SIMILAR_TO]->() WITH s LIMIT 50000 DELETE s RETURN count(*) AS deleted"

NODE_SIMILARITY = """
CALL gds.nodeSimilarity.write($name, {
  relationshipWeightProperty: 'weight',
  topK: $top_k, similarityCutoff: $cutoff, degreeCutoff: $degree_cutoff,
  writeRelationshipType: 'SIMILAR_TO', writeProperty: 'score'
})
YIELD nodesCompared, relationshipsWritten, similarityDistribution
RETURN nodesCompared, relationshipsWritten,
       round(similarityDistribution.mean, 3) AS mean_score, round(similarityDistribution.p50, 3) AS median_score
"""


def delete_in_batches(db: Neo4jClient, query: str) -> int:
    """Run a 'delete up to N and return the count' query until nothing is left; return the total deleted."""
    total = 0
    while (deleted := db.run_write(query)[0]["deleted"]) > 0:
        total += deleted
    return total


def similar_recipes(db: Neo4jClient) -> None:
    """Project the weighted recipe-ingredient graph, run Node Similarity and write SIMILAR_TO."""
    start = time.perf_counter()
    db.run_write(DROP_PROJECTION, {"name": GRAPH_NAME})
    deleted = delete_in_batches(db, DELETE_OLD_SIMILARITIES)
    projection = db.run_write(PROJECT_WEIGHTED, {"name": GRAPH_NAME})[0]
    print(f"  projection: {projection['nodes']:,} nodes, {projection['relationships']:,} relationships")
    try:
        stats = db.run_write(
            NODE_SIMILARITY,
            {"name": GRAPH_NAME, "top_k": TOP_K, "cutoff": SIMILARITY_CUTOFF, "degree_cutoff": DEGREE_CUTOFF},
        )[0]
    finally:
        db.run_write(DROP_PROJECTION, {"name": GRAPH_NAME})
    print(
        f"  node similarity: {stats['nodesCompared']:,} recipes compared, "
        f"{stats['relationshipsWritten']:,} SIMILAR_TO written (old ones deleted: {deleted:,}), "
        f"mean score {stats['mean_score']}, median {stats['median_score']}, {time.perf_counter() - start:.1f} s"
    )


def main() -> None:
    """Run every algorithm."""
    db = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    try:
        if not db.is_alive():
            raise SystemExit(f"Cannot reach Neo4j at {settings.neo4j_uri}. Is it running? (make docker-run)")
        print("similar recipes (Node Similarity, IDF-weighted Jaccard)...")
        similar_recipes(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
