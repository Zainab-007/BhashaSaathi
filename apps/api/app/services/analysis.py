import re

def analyze(text, grade="Grade 2", subject="Environmental Studies"):
    low=text.lower()
    topic="Plants and Water" if "plant" in low or "पौध" in text or "पानी" in text else (text.split(".")[0][:80] if text else "New Lesson")
    concepts=[]
    for word,concept in [("water","water"),("पानी","water"),("plant","plants"),("पौध","plants"),("grow","growth"),("बढ़","growth"),("sun","sunlight"),("सूरज","sunlight")]:
        if word in low or word in text: 
            if concept not in concepts: concepts.append(concept)
    if not concepts: concepts=["main idea"]
    objective=f"Learner can explain the main idea of {topic.lower()} using the key concepts."
    explanation=f"A simple {grade} explanation based only on the teacher source: {text[:400]}"
    activity="Ask the learner to name one example from their surroundings and explain it in their preferred language."
    return {"topic":topic,"concepts":concepts,"learning_objective":objective,"explanation":explanation,"activity":activity}
