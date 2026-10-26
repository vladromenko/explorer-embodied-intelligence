"""MIT Hailo v5 segmentation subset, pinned upstream7b850e0444a142ada88443d675e3c3ddda18bd74.

Copyright(c)2023 Hailo; see adjacent LICENSE and source-manifest.json.
Adaptations: dependency isolation; NumPy equivalent of official Cython NMS;
structured empty-result branch; configurable bounded detections/single-label.
Decode/anchors/mask arithmetic from upstream, no camera or hardware entry point.
"""
import cv2
import numpy as np
from scipy.special import expit


def cnms(dets,thresh):
    """Official Cython +1 pixel IoU/suppression semantics, vectorized NumPy port.

    Preserve upstream score-tie ordering and original-index output exactly.
    """
    if dets.dtype!=np.float32 or dets.ndim!=2 or dets.shape[1]!=5:raise ValueError('NMS needs float32 boxes/scores')
    x1,y1,x2,y2,scores=dets.T
    areas=(x2-x1+1)*(y2-y1+1)
    order=scores.argsort()[::-1];suppressed=np.zeros(len(dets),dtype=bool);selected=[]
    for position,index in enumerate(order):
        if not suppressed[index]:
            selected.append(int(index))
            others=order[position+1:];others=others[~suppressed[others]]
            width=np.maximum(np.float32(0),np.minimum(x2[index],x2[others])-np.maximum(x1[index],x1[others])+1)
            height=np.maximum(np.float32(0),np.minimum(y2[index],y2[others])-np.maximum(y1[index],y1[others])+1)
            intersection=width*height
            overlap=intersection/(areas[index]+areas[others]-intersection)
            suppressed[others[overlap>=np.float32(thresh)]]=True
    # Cython returns unsuppressed original indices; source input is confidence sorted.
    return np.asarray(sorted(selected),dtype=np.int64)

def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def non_max_suppression(prediction, conf_thres=0.25, iou_thres=0.45, max_det=300, nm=32, multi_label=True):
    """Non-Maximum Suppression (NMS) on inference results to reject overlapping detections
    Args:
        prediction: numpy.ndarray with shape (batch_size, num_proposals, 351)
        conf_thres: confidence threshold for NMS
        iou_thres: IoU threshold for NMS
        max_det: Maximal number of detections to keep after NMS
        nm: Number of masks
        multi_label: Consider only best class per proposal or all conf_thresh passing proposals
    Returns:
         A list of per image detections, where each is a dictionary with the following structure:
         {
            'detection_boxes':   numpy.ndarray with shape (num_detections, 4),
            'mask':              numpy.ndarray with shape (num_detections, 32),
            'detection_classes': numpy.ndarray with shape (num_detections, 80),
            'detection_scores':  numpy.ndarray with shape (num_detections, 80)
         }
    """

    assert 0 <= conf_thres <= 1, f"Invalid Confidence threshold {conf_thres}, valid values are between 0.0 and 1.0"
    assert 0 <= iou_thres <= 1, f"Invalid IoU threshold {iou_thres}, valid values are between 0.0 and 1.0"

    nc = prediction.shape[2] - nm - 5  # number of classes
    xc = prediction[..., 4] > conf_thres  # candidates

    max_wh = 7680  # (pixels) maximum box width and height
    mi = 5 + nc  # mask start index
    output = []
    for xi, x in enumerate(prediction):  # image index, image inference
        x = x[xc[xi]]  # confidence
        # If none remain process next image
        if not x.shape[0]:
            output.append(
                {
                    "detection_boxes": np.zeros((0, 4)),
                    "mask": np.zeros((0, 32)),
                    "detection_classes": np.zeros((0, 80)),
                    "detection_scores": np.zeros((0, 80)),
                }
            )
        else:

            # Confidence = Objectness X Class Score
            x[:, 5:] *= x[:, 4:5]

            # (center_x, center_y, width, height) to (x1, y1, x2, y2)
            boxes = xywh2xyxy(x[:, :4])
            mask = x[:, mi:]

            multi_label &= nc > 1
            if not multi_label:
                conf = np.expand_dims(x[:, 5:mi].max(1), 1)
                j = np.expand_dims(x[:, 5:mi].argmax(1), 1).astype(np.float32)

                keep = np.squeeze(conf, 1) > conf_thres
                x = np.concatenate((boxes, conf, j, mask), 1)[keep]
            else:
                i, j = (x[:, 5:mi] > conf_thres).nonzero()
                x = np.concatenate((boxes[i], x[i, 5 + j, None], j[:, None].astype(np.float32), mask[i]), 1)

            # sort by confidence
            x = x[x[:, 4].argsort()[::-1]]

            # per-class NMS
            cls_shift = x[:, 5:6] * max_wh
            boxes = x[:, :4] + cls_shift
            conf = x[:, 4:5]
            preds = np.hstack([boxes.astype(np.float32), conf.astype(np.float32)])
            keep = cnms(preds, iou_thres)

            if keep.shape[0] > max_det:
                keep = keep[:max_det]

            out = x[keep]
            scores = out[:, 4]
            classes = out[:, 5]
            boxes = out[:, :4]
            masks = out[:, 6:]

            out = {"detection_boxes": boxes, "mask": masks, "detection_classes": classes, "detection_scores": scores}

            output.append(out)

    return output


def xywh2xyxy(x):
    y = np.copy(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2
    y[:, 1] = x[:, 1] - x[:, 3] / 2
    y[:, 2] = x[:, 0] + x[:, 2] / 2
    y[:, 3] = x[:, 1] + x[:, 3] / 2
    return y


def _yolov5_decoding(branch_idx, output, stride_list, anchor_list, num_classes):
    BS, H, W = output.shape[0:3]
    stride = stride_list[branch_idx]
    anchors = anchor_list[branch_idx] / stride
    num_anchors = len(anchors) // 2

    grid, anchor_grid = _make_grid(anchors, stride, BS, W, H)

    output = output.transpose((0, 3, 1, 2)).reshape((BS, num_anchors, -1, H, W)).transpose((0, 1, 3, 4, 2))
    xy, wh, conf, mask = np.array_split(output, [2, 4, 4 + num_classes + 1], axis=4)

    # decoding
    xy = (_sigmoid(xy) * 2 + grid) * stride
    wh = (_sigmoid(wh) * 2) ** 2 * anchor_grid

    out = np.concatenate((xy, wh, _sigmoid(conf), mask), 4)
    out = out.reshape((BS, num_anchors * H * W, -1)).astype(np.float32)

    return out


def yolov5_seg_postprocess(endnodes, device_pre_post_layers=None, **kwargs):
    """
    endnodes is a list of 4 tensors:
        endnodes[0]:  mask protos with shape (BS, 160, 160, 32)
        endnodes[1]:  stride 32 of input with shape (BS, 20, 20, 351)
        endnodes[2]:  stride 16 of input with shape (BS, 40, 40, 351)
        endnodes[3]:  stride 8 of input with shape (BS, 80, 80, 351)
    Returns:
        A list of per image detections, where each is a dictionary with the following structure:
        {
            'detection_boxes':   numpy.ndarray with shape (num_detections, 4),
            'mask':              numpy.ndarray with shape (num_detections, 32),
            'detection_classes': numpy.ndarray with shape (num_detections, 80),
            'detection_scores':  numpy.ndarray with shape (num_detections, 80)
        }
    """
    img_dims = tuple(kwargs["input_shape"])
    if kwargs.get("hpp", False):
        # the outputs where decoded by the emulator (as part of the network)
        # organizing the output for evaluation
        return _organize_hpp_yolov5_seg_outputs(endnodes, img_dims=img_dims)

    protos = endnodes[0]
    anchor_list = np.array(kwargs["anchors"]["sizes"][::-1])
    stride_list = kwargs["anchors"]["strides"][::-1]
    num_classes = kwargs["classes"]

    outputs = []
    for branch_idx, output in enumerate(endnodes[1:]):
        decoded_info = _yolov5_decoding(branch_idx, output, stride_list, anchor_list, num_classes)
        outputs.append(decoded_info)

    outputs = np.concatenate(outputs, 1)  # (BS, num_proposals, 117)

    # NMS
    score_thres = kwargs["score_threshold"]
    iou_thres = kwargs["nms_iou_thresh"]
    outputs = non_max_suppression(outputs, score_thres, iou_thres, nm=protos.shape[-1],
        max_det=kwargs.get('max_detections',20),multi_label=kwargs.get('multi_label',False))
    outputs = _finalize_detections_yolov5_seg(outputs, protos, **kwargs)

    # reorder and normalize bboxes
    for output in outputs:
        output["detection_boxes"] = _normalize_yolov5_seg_bboxes(output, img_dims)
    return outputs


def _organize_hpp_yolov5_seg_outputs(outputs, img_dims):
    # the outputs structure is [-1, num_of_proposals, 6 + h*w]
    # were the mask information is ordered as follows
    # [x_min, y_min, x_max, y_max, score, class, flattened mask] of each detection
    # this function separates the structure to informative dict
    predictions = []
    batch_size, num_of_proposals = outputs.shape[0], outputs.shape[-1]
    outputs = np.transpose(np.squeeze(outputs, axis=1), [0, 2, 1])
    for i in range(batch_size):
        predictions.append(
            {
                "detection_boxes": outputs[i, :, :4][:, [1, 0, 3, 2]],
                "detection_scores": outputs[i, :, 4],
                "detection_classes": outputs[i, :, 5],
                "mask": outputs[i, :, 6:].reshape((num_of_proposals, *img_dims)),
            }
        )
    return predictions


def _normalize_yolov5_seg_bboxes(output, img_dims):
    # normalizes bboxes and change the bboxes format to y_min, x_min, y_max, x_max
    bboxes = output["detection_boxes"]
    bboxes[:, [0, 2]] /= img_dims[1]
    bboxes[:, [1, 3]] /= img_dims[0]

    return bboxes


def _finalize_detections_yolov5_seg(outputs, protos, **kwargs):
    for batch_idx, output in enumerate(outputs):
        shape = tuple(kwargs["input_shape"])
        boxes = output["detection_boxes"]
        masks = output["mask"]
        proto = protos[batch_idx]
        masks = process_mask_optimized(proto.astype(np.float32, copy=False), masks.astype(np.float32, copy=False), boxes, shape, upsample=True)
        output["mask"] = masks

    return outputs


def _make_grid(anchors, stride, bs=8, nx=20, ny=20):
    na = len(anchors) // 2
    y, x = np.arange(ny), np.arange(nx)
    yv, xv = np.meshgrid(y, x, indexing="ij")

    grid = np.stack((xv, yv), 2)
    grid = np.stack([grid for _ in range(na)], 0) - 0.5
    grid = np.stack([grid for _ in range(bs)], 0)

    anchor_grid = np.reshape(anchors * stride, (na, -1))
    anchor_grid = np.stack([anchor_grid for _ in range(ny)], axis=1)
    anchor_grid = np.stack([anchor_grid for _ in range(nx)], axis=2)
    anchor_grid = np.stack([anchor_grid for _ in range(bs)], 0)

    return grid, anchor_grid


def process_mask_optimized(protos, masks_in, bboxes, shape, upsample=True, downsample=False):
    mh, mw, c = protos.shape
    ih, iw = shape

    # Matrix multiplication (N, 32) x (32, 160*160) -> (N, 160*160)
    protos_flat = protos.reshape(-1, c).T  # (32, H*W)
    masks = masks_in @ protos_flat  # (N, H*W)
    masks = expit(masks).reshape(-1, mh, mw)  # (N, H, W)

    # Downsample-based crop
    bboxes = bboxes.copy()
    if downsample:
        bboxes[:, [0, 2]] *= mw / iw
        bboxes[:, [1, 3]] *= mh / ih
        masks = crop_mask_roi_vectorized(masks, bboxes)

    # Resize
    if upsample:
        masks = fast_resize_masks(masks, (ih, iw))

    # Final crop if not downsample
    if not downsample:
        masks = crop_mask_roi_vectorized(masks, bboxes)

    return masks


def crop_mask_roi_vectorized(masks, boxes):
    """
    Vectorized cropping of masks with zero-padding outside boxes.
    Args:
        masks: (N, H, W)
        boxes: (N, 4) in pixels
    Returns:
        (N, H, W) cropped masks
    """
    N, H, W = masks.shape
    output = np.zeros_like(masks)

    boxes = np.round(boxes).astype(int)
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, W - 1)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, H - 1)

    for i in range(N):
        x1, y1, x2, y2 = boxes[i]
        output[i, y1:y2, x1:x2] = masks[i, y1:y2, x1:x2]

    return output


def fast_resize_masks(masks, out_shape):
    """
    Resize N masks from (H, W) to out_shape (ih, iw)
    using OpenCV. Input: (N, H, W), Output: (N, ih, iw)
    """
    ih, iw = out_shape
    resized = np.empty((masks.shape[0], ih, iw), dtype=np.float32)
    for i in range(masks.shape[0]):
        resized[i] = cv2.resize(masks[i], (iw, ih), interpolation=cv2.INTER_LINEAR)
    return resized
