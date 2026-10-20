"""Hand-made mapping of Food.com tags to the Course, Diet and Cuisine labels of the graph.

Every tag not listed here is dropped (see docs/graph_model.md, section 5.3).
"""

COURSES = frozenset(
    {
        "main-dish",
        "side-dishes",
        "appetizers",
        "desserts",
        "breakfast",
        "brunch",
        "lunch",
        "salads",
        "soups-stews",
        "beverages",
        "breads",
        "snacks",
        "condiments-etc",
    }
)

DIETS = frozenset(
    {
        "vegetarian",
        "vegan",
        "gluten-free",
        "dairy-free",
        "egg-free",
        "nut-free",
        "lactose",
        "kosher",
        "diabetic",
        "low-carb",
        "very-low-carbs",
        "low-calorie",
        "low-fat",
        "low-sodium",
        "low-cholesterol",
        "low-saturated-fat",
        "high-protein",
        "high-fiber",
        "high-calcium",
    }
)

# Cuisine hierarchy: parent -> children. Used for the (:Cuisine)-[:PART_OF]->(:Cuisine) relationships.
CUISINE_CHILDREN = {
    "north-american": ["american", "canadian", "mexican"],
    "american": [
        "southern-united-states",
        "southwestern-united-states",
        "midwestern",
        "northeastern-united-states",
        "pacific-northwest",
        "californian",
        "cajun",
        "creole",
        "tex-mex",
        "soul",
        "amish-mennonite",
        "pennsylvania-dutch",
        "hawaiian",
        "native-american",
    ],
    "canadian": ["ontario", "quebec", "british-columbian"],
    "mexican": ["oaxacan", "baja"],
    "european": [
        "italian",
        "greek",
        "french",
        "english",
        "german",
        "scandinavian",
        "spanish",
        "scottish",
        "irish",
        "polish",
        "russian",
        "swiss",
        "portuguese",
        "hungarian",
        "dutch",
        "welsh",
        "austrian",
        "belgian",
        "czech",
        "georgian",
    ],
    "scandinavian": ["swedish", "norwegian", "finnish", "danish", "icelandic"],
    "asian": [
        "chinese",
        "japanese",
        "thai",
        "indian",
        "vietnamese",
        "korean",
        "indonesian",
        "filipino",
        "malaysian",
        "pakistani",
        "nepalese",
        "cambodian",
        "laotian",
        "mongolian",
    ],
    "chinese": ["szechuan", "cantonese", "hunan", "beijing"],
    "middle-eastern": ["lebanese", "turkish", "iranian-persian", "saudi-arabian", "iraqi", "palestinian"],
    "african": [
        "moroccan",
        "south-african",
        "egyptian",
        "ethiopian",
        "nigerian",
        "libyan",
        "sudanese",
        "angolan",
        "congolese",
        "somalian",
        "namibian",
    ],
    "south-american": ["brazilian", "argentine", "peruvian", "chilean", "colombian", "venezuelan", "ecuadorean"],
    "central-american": ["costa-rican", "guatemalan", "honduran"],
    "caribbean": ["cuban", "puerto-rican"],
    "south-west-pacific": ["australian", "new-zealand", "polynesian", "micro-melanesia"],
}

# Cuisines that are not part of a bigger one.
STANDALONE_CUISINES = ("jewish-ashkenazi", "jewish-sephardi")

CUISINE_PARENT = {child: parent for parent, children in CUISINE_CHILDREN.items() for child in children}
CUISINES = frozenset(CUISINE_CHILDREN) | frozenset(CUISINE_PARENT) | frozenset(STANDALONE_CUISINES)
