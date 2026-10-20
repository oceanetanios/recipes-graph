# Graph algorithms (GDS)

Run with make gds (scripts/graph_algorithms.py), after make seed. Results are written into the graph, so the API only reads them.

## 1. Similar recipes: Node Similarity

Question: which recipes are closest to this one?

How:
- in-memory projection (GDS): Recipe -[CONTAINS {weight}]-> Ingredient, 48,397 nodes and 363,928 relationships
- weight = IDF of the ingredient = log(number of recipes / recipes using it). Salt (48% of recipes) weighs 0.7, an ingredient used in 100 recipes weighs 6.0
- gds.nodeSimilarity.write with relationshipWeightProperty: weight, so the score is a weighted Jaccard index: sum of the weights of the shared ingredients / sum of the weights of all their ingredients
- topK 10, similarityCutoff 0.1, degreeCutoff 3 (recipes with fewer than 3 ingredients are not compared)
- result: (:Recipe)-[:SIMILAR_TO {score}]->(:Recipe), 407,455 relationships for 40,754 recipes, mean score 0.31, about 3 minutes
- endpoint: GET /api/recipes/{id}/similar, which also returns the shared ingredients (rarest first): the explanation is a real path in the graph, recipe -> ingredient <- recipe

Why weighted: plain Jaccard counts every shared ingredient the same, so salt, onion and garlic make unrelated recipes look similar. Comparison on two recipes (top 3):

    wholly guacamole: lime, tomatoes, garlic clove, cilantro, salt, jalapeno pepper, onion, avocado
      plain Jaccard          0.600  salsa cruda pico de gallo      shares garlic clove, salt, cilantro, jalapeno, onion, tomatoes
                             0.556  simple mexican salsa           shares jalapeno, cilantro, lime, onion, salt
                             0.500  easy authentic guacamole       shares salt, tomatoes, onion, avocado, lime
      IDF-weighted (used)    0.553  whack a mole eh (guacamole)    shares jalapeno, cilantro, avocado, lime, tomatoes
                             0.503  simple mexican salsa           shares jalapeno, cilantro, lime, onion, salt
                             0.503  salsa cruda pico de gallo      shares garlic clove, salt, cilantro, jalapeno, onion, tomatoes

    chicken pesto: breadcrumbs, mushroom, provolone cheese, boneless skinless chicken breast, pesto sauce mix
      plain Jaccard          0.333  low fat yogurt chicken fingers shares breadcrumbs, chicken breast
                             0.250  quick and easy chicken parmesan
                             0.250  fast easy chicken in salsa with zucchini
      IDF-weighted (used)    0.257  low fat yogurt chicken fingers
                             0.248  provolone stuffed pesto burgers shares pesto sauce mix, provolone cheese
                             0.227  chicken garlic bites

Takeaways:
- with the weights, the guacamole's best match is another guacamole (shared avocado, the defining ingredient) instead of a salsa that mostly shares salt and onion
- the pesto burgers come up because they share the two rare ingredients (pesto sauce mix, provolone), even with a different main ingredient
- the weights do the job of a hand-made stop list (removing salt, butter...), without choosing a threshold

Alternatives considered:
- plain Jaccard: rejected, see above
- removing hub ingredients (in more than 10% of recipes, 11 ingredients): works, but the threshold is arbitrary and it throws away information (egg does matter for a cake)
- computing Jaccard in Cypher at request time: fine for one recipe, but comparing all pairs is O(n^2) in Cypher; GDS does it in memory with optimized code, once
- KNN on node embeddings (FastRP): could also use tags and cuisines, kept for the ML step (step 23)
- storing SIMILAR_TO vs computing at request time: stored, because the API answers in milliseconds and the similarity only changes when the data is reloaded. The cost is 407k extra relationships, recomputed by make gds.

## 2. Recipe families: Louvain

Question: which recipes belong to the same family of dishes?

How:
- projection: Recipe nodes with SIMILAR_TO as undirected relationships, weighted by score (41,240 nodes, 814,910 relationships)
- gds.louvain.write with relationshipWeightProperty: score, writes Recipe.community (indexed)
- result: 510 communities, modularity 0.674 (above 0.3 means a real community structure), 40 seconds
- endpoint: GET /api/recipes/{id}/community: size, characteristic ingredients (count in the community x IDF, so salt does not top every family) and the most reviewed recipes of the family

Example: the community of "wholly guacamole" has 2,763 recipes, characteristic ingredients cilantro, salsa, chili powder, sour cream, black beans, cheddar cheese, popular recipes chicken with black beans, chimichangas, burrito casserole: Mexican / Tex-Mex cooking, found without using the cuisine tags.

Why Louvain: it optimizes modularity on weighted graphs and scales to the whole graph in seconds. Label Propagation was the alternative: faster, but its results can change between runs.

## 3. Pathfinding: shortest chain of similar recipes

GET /api/recipes/path?from=..&to=.. runs Cypher shortestPath on SIMILAR_TO (undirected, at most 8 steps). Both ends are bound, so Neo4j searches from both sides (bidirectional BFS) instead of exploring the whole graph. The 8-step limit bounds the cost as the graph grows.

Example: wholly guacamole -> easy authentic mexican guacamole -> vegetable quiche cups -> aunt lil s spinach balls -> chicken pesto (4 steps).
