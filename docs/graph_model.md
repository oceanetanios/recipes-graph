# Graph model

How the Food.com data is turned into a graph, and why. Based on the exploration in notebooks/01_data_exploration.ipynb.

Diagrams (open with draw.io, app.diagrams.net or the "Draw.io Integration" VS Code extension):
- docs/diagrams/graph_schema.drawio: the Neo4j graph schema
- docs/diagrams/er_diagram.drawio: the raw CSV tables we start from


## 1. What the graph must answer

The model is built around these questions, not around the CSV columns:

- what can I cook with the ingredients I have?
- which recipes are similar to this one?
- which ingredients go well together?
- what should this user cook next (recommendations)?
- filter all of that by cuisine, diet, course and cooking time
- answer the same questions in plain English (NL-to-Cypher)


## 2. Schema

Nodes:

    (:User           {id})
    (:Recipe         {id, name, minutes, n_steps, n_ingredients, submitted, description, steps,
                      calories, total_fat_pdv, sugar_pdv, sodium_pdv, protein_pdv, saturated_fat_pdv, carbs_pdv})
    (:Ingredient     {name})
    (:IngredientCategory {name, source})
    (:Cuisine        {name})
    (:Diet           {name})
    (:Course         {name})

Relationships:

    (:User)-[:SUBMITTED]->(:Recipe)
    (:User)-[:REVIEWED {rating, date}]->(:Recipe)
    (:Recipe)-[:CONTAINS]->(:Ingredient)
    (:Ingredient)-[:IN_CATEGORY {match_score}]->(:IngredientCategory)
    (:Recipe)-[:HAS_CUISINE]->(:Cuisine)
    (:Cuisine)-[:PART_OF]->(:Cuisine)
    (:Recipe)-[:FITS_DIET]->(:Diet)
    (:Recipe)-[:SERVED_AS]->(:Course)
    (:Recipe)-[:SIMILAR_TO {score}]->(:Recipe)      computed by GDS, see docs/algorithms.md

7 labels, 9 relationship types (8 loaded from the data, SIMILAR_TO computed).

Notes on properties:
- REVIEWED.rating is 1 to 5, or null when the raw rating was 0 (comment without a rating)
- Recipe.minutes is null when the raw value is 0 or more than a week (placeholders like 2147483647)
- nutrition values are null when they are clearly wrong (for example more than 10000 calories)
- steps is kept as a list property, only for display
- IngredientCategory.source = "USDA FoodData Central", match_score says how sure the name matching was (0 to 1)


## 3. Constraints and indexes

Uniqueness constraints (they also create an index, and make MERGE fast during the import):

    User.id, Recipe.id, Ingredient.name, IngredientCategory.name, Cuisine.name, Diet.name, Course.name

Extra indexes, for the queries we expect:
- range index on Recipe.minutes: "recipes under 30 minutes"
- range index on Recipe.calories: nutrition filters
- full-text index on Recipe.name: search recipes by words, and help the LLM find the right recipe from a fuzzy name
- full-text index on Ingredient.name: "tomato" should also find "cherry tomatoes"

Recipe.name is not a constraint because it is not unique (1451 duplicate names).


## 4. Which data we load

Recipes and users with at least 5 reviews, filtered until stable (removing a user can push a recipe under 5, and the other way round):

- 41,240 recipes
- 555,618 reviews
- 20,187 users (17,813 reviewers, plus the authors of the kept recipes)
- 7,976 raw ingredient names, 7,157 after cleaning, 363,928 CONTAINS relationships
- 106 cuisines, 61,179 FITS_DIET, 53,724 SERVED_AS and 41,791 HAS_CUISINE relationships

The cleaning is done by scripts/clean_data.py (make prepare-data), with the tag mapping in scripts/tag_groups.py.

Why: 73% of users reviewed only once. They add nothing to similarity or recommendations, they only make GDS slower and noisier. The threshold is a parameter of the import script, so the full dataset can still be loaded.


## 5. Decisions and rejected alternatives

5.1 Ingredients are nodes
- chosen: (:Recipe)-[:CONTAINS]->(:Ingredient)
- rejected: a list property on Recipe
- why: one ingredient is shared by thousands of recipes (salt is in 86k). As nodes, "recipes that share ingredients with X" is a 2-hop traversal. As a list, it means scanning every recipe and comparing lists. Node Similarity in GDS also needs them as nodes.
- names are cleaned before loading (eggs -> egg, "salt and pepper" split in two), otherwise egg and eggs would be two different nodes.

5.2 Tags split into Cuisine, Diet and Course
- chosen: three labels, only for the useful tags
- rejected 1: tags as a list property (same problem as ingredients, no traversal, no GDS)
- rejected 2: one generic :Tag label for all 552 tags
- why: the tags are not one thing. "italian" (where it comes from), "vegetarian" (who can eat it) and "desserts" (when you eat it) answer different questions. With separate labels, "vegetarian italian desserts" is a clear pattern, the LLM gets a readable schema, and each family can have its own structure (only Cuisine has a PART_OF hierarchy: italian -> european).
- coverage: 94% of recipes have a course tag, 49% a diet tag, 39% a cuisine tag.

5.3 Tags we drop
- meta headers on almost every recipe: preparation, time-to-make, course, cuisine, dietary, main-ingredient, occasion...
- tags that repeat a column: 15-minutes-or-less, 30-minutes-or-less... (= minutes), 5-ingredients-or-less (= n_ingredients), 3-steps-or-less (= n_steps)
- main-ingredient tags like chicken or beef: already covered by CONTAINS
- occasion, season and equipment tags (christmas, summer, oven...): could be another label later, left out to keep the model focused

5.4 One User label for authors and reviewers
- chosen: one :User with SUBMITTED and REVIEWED
- rejected: separate :Contributor and :Reviewer labels
- why: contributor_id and user_id are the same food.com accounts (17,777 of 27,926 contributors also review). Splitting them would create two nodes for the same person.

5.5 Reviews are relationships, not nodes
- chosen: (:User)-[:REVIEWED {rating, date}]->(:Recipe)
- rejected: a (:Review) node between User and Recipe
- why: a review only links one user to one recipe, nothing else points to it. As a node it would add 555k nodes and one extra hop to every recommendation query. The review text is not stored (big, and not needed for our questions).
- one relationship type with a nullable rating, instead of RATED + REVIEWED: queries stay simple, and averages just ignore nulls.

5.6 Nutrition and time are properties
- chosen: properties on Recipe
- rejected: (:Nutrient) nodes with an amount on the relationship
- why: the values belong to one recipe and are never shared. Properties can be filtered with a range index ("under 400 calories"). Nodes would only add hops.

5.7 Steps are not nodes
- rejected: (:Step)-[:NEXT]->(:Step) chains
- why: none of our questions is about the order of steps. They are kept as a list for display.


## 6. Second data source: ingredient categories

The Food.com tables have no notion of "dairy", "vegetable" or "meat". We add it from USDA FoodData Central (public, free), which classifies foods into about 25 categories (Dairy and Egg Products, Vegetables and Vegetable Products, Spices and Herbs...).

Each cleaned ingredient name is matched to a USDA food, and gets the category of that food. The matching is approximate, so the relationship keeps a match_score, and low scores are not loaded. This also gives the LLM a way to answer "recipes without meat" or "which dairy products are used the most".

Status: designed but not loaded (no IN_CATEGORY relationships in the current graph). The label and its constraint exist so it can be added without changing the model.


## Appendix: anti-genericity

The project brief asks to compare our schema with a basic schema generated by an AI assistant from the same description.

Prompt used, in a fresh chat with an AI assistant, with no other context:

    Design a Neo4j graph schema for a "Recipe & Ingredient Explorer" built on the Food.com
    "Recipes and User Interactions" dataset (Kaggle). Give the node labels, relationship
    types and main properties.

AI-generated schema (summary of its answer):

    (:User)-[:SUBMITTED]->(:Recipe)<-[:REVIEWED {rating, date, review}]-(:User)
    (:Recipe)-[:CONTAINS {position, raw_text}]->(:Ingredient)-[:VARIANT_OF]->(:IngredientGroup)
    (:Recipe)-[:TAGGED]->(:Tag)-[:IN_CATEGORY]->(:TagCategory)
    (:Recipe)-[:HAS_STEP]->(:Step)-[:NEXT]->(:Step)          optional
    (:Ingredient)-[:PAIRS_WITH {count, pmi}]-(:Ingredient)    derived
    (:Recipe)-[:SIMILAR_TO {score}]-(:Recipe)                 derived

    Recipe: id, name, description, minutes, submitted, n_steps, n_ingredients, steps,
            7 nutrition values, avg_rating, n_ratings (stored)
    User: id, n_reviews, n_submitted, avg_rating_given (stored)
    Tag: name, for all tags (optionally a second label :Tag:Cuisine, :Tag:Diet)
    load: neo4j-admin import of the full dataset

### A. Where we agree

Same choice, reached independently in step 15:
- one User for authors and reviewers, SUBMITTED and REVIEWED
- ratings on the REVIEWED relationship, not on a Review node, and rating 0 means "no rating"
- ingredients as nodes, with normalized names (otherwise "garlic clove" and "garlic cloves" are two nodes)
- nutrition and minutes as Recipe properties with range indexes, steps as a list property
- SIMILAR_TO precomputed with GDS Node Similarity

The generic answer is good on these points, so we kept them. The differences below are where our own data exploration led somewhere else.

### B. Where our model differs and why

1. Tags: one generic :Tag (+ TagCategory) vs our Cuisine, Diet and Course labels
   - the AI keeps all 552 tags. Our exploration showed that the top ones (preparation 99.5%, time-to-make 97%, course 94%) are category headers on almost every recipe: as Tag nodes they would be giant hubs with no information, and they would dominate similarity and communities
   - time tags (15-minutes-or-less...) repeat the minutes property
   - cuisine, diet and course answer different questions, so separate labels give a clear schema for the LLM and simple patterns: (r)-[:FITS_DIET]->(:Diet {name: 'vegan'}) instead of going through TagCategory
   - only cuisines form a hierarchy (italian PART_OF european, szechuan PART_OF chinese PART_OF asian), which a flat Tag cannot express. The AI schema has no way to ask for "asian" and get chinese recipes
2. Similarity weighted by IDF vs plain Jaccard
   - the AI suggests Jaccard on ingredients. With plain Jaccard, salt (48% of recipes) and onion make unrelated recipes look similar; our IDF weights make the guacamole's best match another guacamole instead of a salsa (docs/algorithms.md)
3. No stored averages (avg_rating, n_ratings, user stats)
   - we compute them at query time with COUNT { } and avg(): degrees are cheap in Neo4j, and stored values would have to be updated every time a review is added. Trade-off accepted: sorting all recipes by average rating is slower
4. No review text, no CONTAINS position or raw_text
   - none of our questions uses them; the review text alone would add hundreds of MB. The cleaning is deterministic, so the raw strings can be recomputed from the CSV
5. No IngredientGroup from ingr_map.pkl
   - that file is not one of the two we use. Our cleaning merges names only into names that already exist in the data (never invents a group), and the category level we designed (IngredientCategory, USDA) is about food type (dairy, vegetable), not spelling variants
6. No PAIRS_WITH
   - ingredient pairings are a 2-hop query on demand (basil -> recipes -> other ingredients) and the agent can write it; storing every pair would add a dense layer for one question. We spent the precomputation on SIMILAR_TO and Louvain communities instead
7. A filtered subset, loaded with UNWIND + MERGE, instead of neo4j-admin import of everything
   - 73% of users reviewed once: they only add noise to similarity and communities. 41k recipes load in 3 minutes, and MERGE makes the import safe to run again (neo4j-admin import only works on an empty database)

### C. Takeaway

The AI schema is a reasonable generic starting point. Our model differs where the exploration of this dataset showed something the generic answer could not know: meta tags on every recipe, hub ingredients, a cuisine hierarchy, one-off users, and which questions the API actually answers.
