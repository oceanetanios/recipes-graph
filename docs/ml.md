# ML workflow: is this recipe vegetarian?

Node classification from graph embeddings. Code: scripts/ml_diet.py (make ml).

## 1. How

- GDS projection: Recipe -[CONTAINS]- Ingredient, undirected. Diet nodes are not projected, so the model never sees the label (no leakage)
- GDS FastRP: one 512-number embedding per recipe, iteration weights [0.0, 1.0, 0.5] (the recipe's ingredients, then the recipes sharing them), relationships weighted by ingredient IDF, random seed fixed
- settings chosen by comparing test ROC AUC: 128d no weight 0.672, 256d 0.666, 512d with IDF 0.679, 512d with IDF and a second hop 0.704 (kept)
- label: the recipe has the vegetarian tag
- 41,240 recipes, 17.1% vegetarian, split 80% train / 20% test (stratified)
- model: scikit-learn standard scaling + logistic regression with balanced class weights (vegetarian is the minority class)

## 2. Results (test set)

| model | metrics |
|---|---|
| baseline (random, same class balance) | accuracy 0.722, precision 0.176, recall 0.17, f1 0.173, roc_auc 0.503 |
| logistic regression on FastRP embeddings | accuracy 0.664, precision 0.286, recall 0.64, f1 0.395, roc_auc 0.704 |

## 3. Recipes predicted vegetarian but not tagged (test set)

The tag is only present on part of the data, so some of these are probably vegetarian recipes that nobody tagged: the model can suggest missing tags.

- pomegranate honey quinoa breakfast (0.95)
- tuna curry (0.93)
- simmered black beans (0.93)
- noodle salad with peanut lime vinaigrette (0.93)
- almond power bar (0.92)
- avocado b l t salad (0.92)
- zucchini tomato skillet (0.92)
- days of summer oatmeal (0.91)
- roasted cherry or grape tomatoes (0.91)
- strawberry limeade slush weight watcher s style (0.91)

## 4. Limits

- a recipe without the tag is not always non-vegetarian, so the labels are noisy and precision is underestimated
- FastRP is random-projection based: results change a little with the seed and the dimension
