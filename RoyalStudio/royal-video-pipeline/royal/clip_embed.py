"""CLIP image/text embeddings (open_clip) for semantic search like "sad moment at a funeral"."""
import numpy as np
import torch
from PIL import Image


class ClipEmbedder:
    def __init__(self, model_name: str, pretrained: str):
        import open_clip
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained, device=self.device)
        self.model.eval()
        if self.device == "cuda":
            self.model.half()
        self.tokenizer = open_clip.get_tokenizer(model_name)
        self.name = f"{model_name}/{pretrained}"

    @torch.no_grad()
    def images(self, rgbs: list[np.ndarray]) -> np.ndarray:
        batch = torch.stack([self.preprocess(Image.fromarray(x)) for x in rgbs]).to(self.device)
        if self.device == "cuda":
            batch = batch.half()
        v = self.model.encode_image(batch).float()
        v = v / v.norm(dim=-1, keepdim=True)
        return v.cpu().numpy()

    @torch.no_grad()
    def text(self, queries: list[str]) -> np.ndarray:
        toks = self.tokenizer(queries).to(self.device)
        v = self.model.encode_text(toks).float()
        v = v / v.norm(dim=-1, keepdim=True)
        return v.cpu().numpy()
