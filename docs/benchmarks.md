# Benchmarks

Do the indexes from scripts/seed_data.py actually help? Measured with scripts/benchmark.py (make benchmark).

How:
- each query runs twice: once normally (Neo4j uses the index) and once with USING SCAN, which forces Neo4j to read every node of the label instead
- db hits come from PROFILE: how many times Neo4j touched the storage. It is the main measure, it does not depend on how busy the PC is
- time is the median of 7 runs after one warm-up run, measured from Python (so it includes about 10 ms of network/driver overhead)
- data: 41,240 recipes, 7,157 ingredients, ~1.1M relationships. Neo4j 5 in Docker on a laptop

## 1. Index vs scan (2026-10-20)

    query                                          version     db hits  median ms  first operator
    ingredient by name (unique constraint)         index             3       15.2  NodeUniqueIndexSeek
    ingredient by name (unique constraint)         scan         14,316       44.1  NodeByLabelScan
    recipe by id (unique constraint)               index             1       12.3  NodeUniqueIndexSeek
    recipe by id (unique constraint)               scan         82,481      120.8  NodeByLabelScan
    recipes under 10 minutes (range index)         index         3,489       16.4  NodeIndexSeekByRange
    recipes under 10 minutes (range index)         scan         82,481       90.2  NodeByLabelScan
    low-calorie recipes (range index)              index         1,559       15.4  NodeIndexSeekByRange
    low-calorie recipes (range index)              scan         82,481      139.5  NodeByLabelScan
    recipe name search (full-text index)           index            10       21.0  ProcedureCall
    recipe name search (full-text index)           scan         23,083       40.2  NodeByLabelScan
    recipes with egg and parmesan (index anchor)   index        20,444       26.0  NodeUniqueIndexSeek
    recipes with egg and parmesan (index anchor)   scan      1,125,274      267.3  NodeByLabelScan, NodeUniqueIndexSeek

Takeaways:
- lookups by id or name: 1 to 3 db hits with the unique constraint index, against one hit per node with a scan (82,481 = 2 x 41,240 recipes). The cost of a seek stays the same as the graph grows, the scan grows with it.
- range filters (minutes, calories): the index only touches the matching recipes (3,489 under 10 minutes) instead of all of them. The gain is bigger when the filter is selective.
- name search: the full-text index answers in 10 db hits. The CONTAINS version is not exactly the same query (it needs the exact substring, full-text matches words in any order), but it shows the cost of reading every name.
- pattern query: starting from the two ingredient nodes (index seek) and walking their CONTAINS relationships costs 20k db hits. Starting from every recipe costs 1.1M (55 times more). This is why queries should be anchored on an indexed node.
- times are small for both versions because the whole graph fits in memory. The db hits show the real difference, and it would grow with the full dataset (231k recipes).

## 2. The two PROFILE plans of the egg and parmesan query

Read from the bottom up: each operator feeds the one above it.

With the index (20,444 db hits):

    ProduceResults                 rows         1  db hits         0
      EagerAggregation             rows         1  db hits         0
        Filter                     rows       571  db hits         0
          NodeHashJoin             rows       571  db hits         0
            Filter                 rows     8,694  db hits     8,694
              Expand(All)          rows     8,694  db hits     8,696
                NodeUniqueIndexSeek rows         1  db hits         2    <- finds "egg" with the index
            Expand(All)            rows     3,048  db hits     3,050
              NodeUniqueIndexSeek  rows         1  db hits         2    <- finds "parmesan cheese" with the index

Forced to scan (1,125,274 db hits):

    ProduceResults                 rows         1  db hits         0
      EagerAggregation             rows         1  db hits         0
        Filter                     rows       571  db hits    28,403
          Expand(All)              rows    30,880  db hits    75,714
            Expand(Into)           rows     3,048  db hits   979,914
              CartesianProduct     rows    41,240  db hits         0
                NodeUniqueIndexSeek rows         1  db hits         2
                NodeByLabelScan    rows    41,240  db hits    41,241    <- reads all 41,240 recipes

Takeaways:
- with the index, Neo4j finds the 2 ingredients directly, walks their CONTAINS relationships (8,694 recipes with egg, 3,048 with parmesan) and joins the two lists (NodeHashJoin): 571 recipes have both.
- with the scan, it pairs every recipe with the parmesan node (CartesianProduct, 41,240 pairs) and then checks for each pair if they are connected (Expand(Into), almost 1M db hits). Same 571 results, 55 times more work.
- a CartesianProduct in a plan is a warning sign: it means two parts of the pattern are not connected, so Neo4j combines everything with everything.

## 3. Query optimizations

To be added with the API endpoints (step 24): before/after for the rewritten queries (LIMIT, pagination, subqueries, avoiding cartesian products).
