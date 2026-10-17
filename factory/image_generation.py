"""Image requests selected by the saved provider, without graph translation."""
from studio.graphs import flux_image


def image_request(profile, prompt, seed, width=768, height=1344, references=(), prefix="images/frame"):
    if profile.get("provider") != "comfyui_qwen_image21":
        return flux_image(prompt, seed, width, height, references, prefix)
    from .qwen_image21 import validate_request
    request = validate_request({"prompt": prompt, "seed": seed, "width": width, "height": height,
                                "steps": profile.get("steps", 8)})
    request.pop("inputs")
    if len(references) > 4:
        raise ValueError("Qwen-Image-2.1 支持最多4张参考图，请先准备同框组合")
    return {"kind": "qwen_image21", **request, "references": list(references)}
