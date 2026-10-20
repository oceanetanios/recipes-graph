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

7 labels, 8 relationship types.

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

Exact USDA file and matching method: to confirm in step 16.


## Appendix: anti-genericity

The project brief asks to compare our schema with a basic schema generated by an AI assistant from the same description.

Prompt used (paste into a fresh chat with an AI assistant, with no other context):

    Design a Neo4j graph schema for a "Recipe & Ingredient Explorer" built on the Food.com
    "Recipes and User Interactions" dataset (Kaggle). Give the node labels, relationship
    types and main properties.

AI-generated schema:

    (to paste here)

Where our model differs and why:

    (to fill after reading the AI schema, using the decisions in section 5)
