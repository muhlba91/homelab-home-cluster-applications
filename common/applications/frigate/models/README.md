# Export D-FINE model to ONNX

## Generate ONNX model

> **ℹ️ Note:** Installing required packages in the notebook may take a long time.

Follow the instructions found at [https://docs.frigate.video/configuration/object_detectors/#downloading-d-fine-model](https://docs.frigate.video/configuration/object_detectors/#downloading-d-fine-model) and [https://github.com/Peterande/D-FINE?tab=readme-ov-file#tools](https://github.com/Peterande/D-FINE?tab=readme-ov-file#tools) (see *Deployment*).

Choose `D-FINE-S` with `Objects365+COCO`.

### Example

```bash
python tools/deployment/export_onnx.py -c configs/dfine/objects365/dfine_hgnetv2_s_obj2coco.yml -r dfine_s_obj2coco.pth
```

## Store ONNX model

Store the generated ONNX model on the S3 bucket used to pull the models.
