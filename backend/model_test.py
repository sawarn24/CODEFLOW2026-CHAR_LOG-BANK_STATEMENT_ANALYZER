from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch
import json

MODEL_PATH = "../finsight_model"

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
model = AutoModelForSequenceClassification.from_pretrained(MODEL_PATH)

with open(f"{MODEL_PATH}/label_map.json") as f:
    label_map = json.load(f)

id2label = label_map["id2label"]


def predict_category(text):
    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        padding=True
    )

    with torch.no_grad():
        outputs = model(**inputs)

    pred = outputs.logits.argmax(dim=1).item()

    return id2label[str(pred)]


print(label_map.keys())

print(predict_category("zomato "))
print(predict_category("SWIGGY "))