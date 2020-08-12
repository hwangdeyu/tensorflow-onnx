# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT license.

"""
math
"""

from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import logging

import numpy as np
from onnx import onnx_pb
from tf2onnx import constants, utils
from tf2onnx.handler import tf_op
from tf2onnx.onnx_opset import common

logger = logging.getLogger(__name__)


# pylint: disable=unused-argument,missing-docstring

@tf_op(["Add", "AddV2", "Div", "Mul", "Sub"])
class BroadcastOp(common.BroadcastOp):
    pass


@tf_op(["RealDiv", "TruncateDiv"], onnx_op="Div")
class RealDiv(common.BroadcastOp):
    pass


@tf_op(["LeakyRelu", "LogSoftmax", "Softplus", "Softsign"])
class DirectOpSinceOpset1:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        pass


@tf_op(["Abs", "Ceil", "Elu", "Exp", "Floor", "Log", "Neg", "Relu", "Sigmoid", "Sqrt",
        "Tanh", "Reciprocal"])
class DirectOp:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        pass

    @classmethod
    def version_6(cls, ctx, node, **kwargs):
        pass


@tf_op(["Acos", "Asin", "Atan", "Cos", "Sin", "Tan"])
class TrigOpSinceOpset7:
    @classmethod
    def version_7(cls, ctx, node, **kwargs):
        pass


@tf_op(["Acosh", "Asinh", "Atanh", "Cosh", "Sinh"])
class TrigOpSinceOpset9:
    @classmethod
    def version_9(cls, ctx, node, **kwargs):
        pass


def make_min_or_max_op(ctx, op_type, inputs, outputs,
                       output_shapes=None, output_dtypes=None):
    # support more dtype
    supported_dtypes = [
        onnx_pb.TensorProto.FLOAT,
        onnx_pb.TensorProto.FLOAT16,
        onnx_pb.TensorProto.DOUBLE
    ]
    target_dtype = onnx_pb.TensorProto.FLOAT
    need_cast = False
    cast_inputs = []
    for inp in inputs:
        dtype = ctx.get_dtype(inp)
        utils.make_sure(dtype is not None, "dtype of {} is None".format(inp))
        if dtype not in supported_dtypes:
            cast_inp = ctx.make_node("Cast", [inp], attr={"to": target_dtype})
            cast_inputs.append(cast_inp.output[0])
            need_cast = True
        else:
            cast_inputs.append(inp)
    node = ctx.make_node(op_type, cast_inputs, shapes=output_shapes)
    actual_outputs = node.output
    if need_cast:
        origin_dtype = ctx.get_dtype(inputs[0])
        if output_dtypes is not None:
            origin_dtype = output_dtypes[0]
        ctx.set_dtype(node.output[0], target_dtype)
        cast_name = utils.make_name(node.name)
        cast_node = ctx.insert_new_node_on_output("Cast", node.output[0], name=cast_name, to=origin_dtype)
        ctx.set_dtype(cast_node.output[0], origin_dtype)
        ctx.copy_shape(node.output[0], cast_node.output[0])
        actual_outputs = cast_node.output
    final_node = ctx.make_node("Identity", actual_outputs, outputs=outputs,
                               shapes=output_shapes, dtypes=output_dtypes)

    # tensorflow minimum/maximum does support broadcast, onnx < opset 8 does not.
    # handle this by doing something like:
    # y = min(x1, add(x2, sub(x1, x1))), where x1, x2 are the inputs and x2 is a scalar
    # this will create a tensor of zeros of the shape of x1, adds x2 to it (which broadcasts) and use that for min.
    shapeo = ctx.get_shape(node.output[0])
    needs_broadcast_op = []
    has_correct_shape = []
    if ctx.opset < 8:
        for i, input_name in enumerate(node.input):
            if ctx.get_shape(input_name) != shapeo:
                needs_broadcast_op.append(i)
            else:
                has_correct_shape.append(input_name)
    if needs_broadcast_op:
        has_correct_shape = has_correct_shape[0]
        for i in needs_broadcast_op:
            input_node = node.inputs[i]
            # get a tensor with zeros (since there is no Fill op as of opset8)
            sub_node = ctx.make_node("Sub", [has_correct_shape, has_correct_shape],
                                     op_name_scope=input_node.name)
            # use add as 'broadcast' op
            add_node = ctx.make_node("Add", [input_node.output[0], sub_node.output[0]],
                                     op_name_scope=input_node.name)
            ctx.replace_input(node, node.input[i], add_node.output[0], i)
    return final_node


@tf_op("Minimum", onnx_op="Min")
@tf_op("Maximum", onnx_op="Max")
class MinMaxOp:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        shapes = node.output_shapes
        dtypes = node.output_dtypes
        ctx.remove_node(node.name)
        make_min_or_max_op(ctx, node.type, node.input, node.output, shapes, dtypes)

    @classmethod
    def version_12(cls, ctx, node, **kwargs):
        pass # support all numeric types and broadcasting

@tf_op("ClipByValue")
class ClipByValueOp:
    # in tf-1.8 there was a ClipByValue op which in later versions was replaced by max(min(x, a), b)
    # To support models generated with tf-1.8 rewrite the tf ClipByValue op to max(min(x, a), b)
    @classmethod
    def version_8(cls, ctx, node, **kwargs):
        supported = [onnx_pb.TensorProto.FLOAT16, onnx_pb.TensorProto.FLOAT, onnx_pb.TensorProto.DOUBLE]
        # fetch those upfront since they are not accessible once we remove 'node'
        shapes = node.output_shapes
        dtypes = node.output_dtypes
        input_dtype = node.inputs[0].output_dtypes[0]
        name = node.name
        min_node = node.inputs[1]
        if min_node.output_dtypes[0] not in supported:
            # cast min if needed
            min_node = ctx.insert_new_node_on_input(node, "Cast", min_node.output[0], to=onnx_pb.TensorProto.FLOAT)
        max_node = node.inputs[2]
        if max_node.output_dtypes[0] not in supported:
            # cast max if needed
            max_node = ctx.insert_new_node_on_input(node, "Cast", max_node.output[0], to=onnx_pb.TensorProto.FLOAT)
        ctx.remove_node(name)
        new_node = ctx.make_node("Max", [node.input[0], min_node.output[0]], outputs=[node.output[0]],
                                 shapes=shapes, dtypes=dtypes)
        if input_dtype not in supported:
            # cast the data tensor if needed
            ctx.insert_new_node_on_input(new_node, "Cast", new_node.input[0], to=onnx_pb.TensorProto.FLOAT)

        new_node = ctx.insert_new_node_on_output("Min", new_node.output[0], name=utils.make_name(name))
        new_node.input.append(max_node.output[0])
        # copy shape and type
        ctx.set_dtype(new_node.output[0], dtypes[0])
        ctx.set_shape(new_node.output[0], shapes[0])
        if dtypes[0] not in supported:
            # cast output if needed
            new_node = ctx.insert_new_node_on_output("Cast", new_node.output[0],
                                                     name=utils.make_name(name), to=dtypes[0])
            # copy shape and type
            ctx.set_dtype(new_node.output[0], dtypes[0])
            ctx.set_shape(new_node.output[0], shapes[0])

    @classmethod
    def version_12(cls, ctx, node, **kwargs):
        node.name = 'Clip' # clip supports all types now

@tf_op("Softmax")
class Softmax:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        # T output = Softmax(T logits). The axis softmax would be performed on is always on -1.
        # T output = Softmax(T input, @int axis). Default axis is 1.
        logits_rank = len(ctx.get_shape(node.input[0]))
        node.set_attr("axis", logits_rank - 1)

    @classmethod
    def version_11(cls, ctx, node, **kwargs):
        cls.version_1(ctx, node, **kwargs)


@tf_op("Square")
class Square:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        node.type = "Mul"
        node.input.append(node.input[0])


@tf_op("Relu6")
class Relu6:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        # relu6 = min(max(features, 0), 6)
        # relu6 = min(max(features, 0), 6)
        node.type = "Clip"
        node.set_attr("min", 0.0)
        node.set_attr("max", 6.0)

    @classmethod
    def version_11(cls, ctx, node, **kwargs):
        # add min and max as inputs
        node.type = "Clip"
        onnx_dtype = ctx.get_dtype(node.input[0])
        np_dtype = utils.ONNX_TO_NUMPY_DTYPE[onnx_dtype]
        clip_min = ctx.make_const(utils.make_name("{}_min".format(node.name)), np.array(0.0, dtype=np_dtype))
        clip_max = ctx.make_const(utils.make_name("{}_max".format(node.name)), np.array(6.0, dtype=np_dtype))
        node.input.append(clip_min.output[0])
        node.input.append(clip_max.output[0])


@tf_op("Rsqrt")
class Rsqrt:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        node.type = "Sqrt"
        op_name = utils.make_name(node.name)
        reciprocal = ctx.insert_new_node_on_output("Reciprocal", node.output[0], name=op_name)
        ctx.copy_shape(node.output[0], reciprocal.output[0])


@tf_op("SquaredDifference")
class SquaredDifference:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        node.type = "Sub"
        op_name = utils.make_name(node.name)
        mul = ctx.insert_new_node_on_output("Mul", node.output[0], name=op_name)
        mul.input.append(node.output[0])


@tf_op("Sign")
class Sign:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        """Sign op."""
        # T sign = Sign(T Input)
        node_dtype = ctx.get_dtype(node.output[0])
        utils.make_sure(node_dtype, "Dtype of {} is None".format(node.name))
        if node_dtype in [onnx_pb.TensorProto.COMPLEX64, onnx_pb.TensorProto.COMPLEX128]:
            raise ValueError("dtype " + str(node_dtype) + " is not supported in onnx for now")
        zero_name = utils.make_name("{}_zero".format(node.name))
        ctx.make_const(zero_name, np.array(0, dtype=np.float32))
        if node_dtype not in [onnx_pb.TensorProto.FLOAT16, onnx_pb.TensorProto.FLOAT, onnx_pb.TensorProto.DOUBLE]:
            cast_node_0 = ctx.make_node("Cast", [node.input[0]], {"to": onnx_pb.TensorProto.FLOAT})
            greater_node = ctx.make_node("Greater", [cast_node_0.output[0], zero_name])
            less_node = ctx.make_node("Less", [cast_node_0.output[0], zero_name])
        else:
            greater_node = ctx.make_node("Greater", [node.input[0], zero_name])
            less_node = ctx.make_node("Less", [node.input[0], zero_name])
        cast_node_1 = ctx.make_node("Cast", [greater_node.output[0]], {"to": node_dtype})
        cast_node_2 = ctx.make_node("Cast", [less_node.output[0]], {"to": node_dtype})

        shapes = node.output_shapes
        dtypes = node.output_dtypes
        ctx.remove_node(node.name)
        ctx.make_node("Sub", [cast_node_1.output[0], cast_node_2.output[0]], outputs=[node.output[0]],
                      shapes=shapes, dtypes=dtypes)

    @classmethod
    def version_9(cls, ctx, node, **kwargs):
        node_dtype = ctx.get_dtype(node.output[0])
        utils.make_sure(node_dtype, "dtype of {} is None".format(node.name))
        if node_dtype in [onnx_pb.TensorProto.BOOL, onnx_pb.TensorProto.COMPLEX64, onnx_pb.TensorProto.COMPLEX128]:
            raise ValueError("dtype " + str(node_dtype) + " is not supported in onnx for now")


@tf_op("Pow")
class Pow:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        if ctx.is_target(constants.TARGET_CAFFE2):
            # workaround a bug in caffe2 pre Feb2018, pow(a, b) becomes np.exp(np.log(a) * b)
            node.type = "Log"
            b = node.input[1]
            ctx.remove_input(node, node.input[1], 1)
            op_name = utils.make_name(node.name)
            mul_op = ctx.insert_new_node_on_output("Mul", node.output[0], name=op_name)
            mul_op.input.append(b)
            op_name = utils.make_name(node.name)
            exp_op = ctx.insert_new_node_on_output("Exp", mul_op.output[0], name=op_name)
            ctx.copy_shape(node.output[0], exp_op.output[0])
            BroadcastOp.version_1(ctx, mul_op, **kwargs)

    @classmethod
    def version_7(cls, ctx, node, **kwargs):
        pass


@tf_op("LRN")
class LRN:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        # ONNX: Each input value is divided by (bias+(alpha/size)*sum(xi^2 for every xi in the local region))^beta
        # TF: sqr_sum[a, b, c, d] = sum(input[a, b, c, d - depth_radius : d + depth_radius + 1] ** 2)
        #     output = input / (bias + alpha * sqr_sum) ** beta

        # by default, depth_radius is 5 in tensorflow
        size = node.get_attr_value("depth_radius", 5) * 2 + 1

        node.set_attr("size", size)
        node.set_attr("alpha", size * node.get_attr("alpha").f)

        shapes = node.output_shapes[0]
        dtypes = node.output_dtypes[0]

        ctx.insert_new_node_on_input(node, "Transpose", node.input[0], perm=constants.NHWC_TO_NCHW)
        ctx.update_node_shape_dtype(node, override=True)
        op_name = utils.make_name(node.name)
        ctx.insert_new_node_on_output("Transpose", node.output[0], perm=constants.NCHW_TO_NHWC,
                                      name=op_name, shapes=shapes, dtypes=dtypes)


@tf_op(["MatMul", "BatchMatMul", "BatchMatMulV2"])
class MatMul:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        # tensorflow allows transpose and conjugated. If found, insert the required transpose.
        # We could use Gemm as well but tensorflow does not pass bias in matmul.
        node.type = "MatMul"

        attrs = ["transpose_a", "transpose_b", "adjoint_a", "adjoint_b", "adj_x", "adj_y"]
        attrs_val = [node.get_attr(attr) for attr in attrs]
        attrs_val = [0 if val is None else val.i for val in attrs_val]

        dtype = ctx.get_dtype(node.output[0])
        if any(attrs_val[2:]):
            # conjugation operation on complex data not supported in onnx for now
            # so if it's complex than raise exception
            if dtype not in [onnx_pb.TensorProto.FLOAT, onnx_pb.TensorProto.FLOAT16, onnx_pb.TensorProto.DOUBLE]:
                raise ValueError("dtype " + dtype + " is not supported in onnx matmul for now")

        transpose_a = (attrs_val[0] + attrs_val[2] + attrs_val[4]) % 2
        transpose_b = (attrs_val[1] + attrs_val[3] + attrs_val[5]) % 2

        if transpose_a != 0:
            shape = ctx.get_shape(node.input[0])
            if shape:
                perm = list(range(0, len(shape)))
                tmp = perm[-1]
                perm[-1] = perm[-2]
                perm[-2] = tmp
                ctx.insert_new_node_on_input(node, "Transpose", node.input[0], perm=perm)

        if transpose_b != 0:
            shape = ctx.get_shape(node.input[1])
            if shape:
                perm = list(range(0, len(shape)))
                tmp = perm[-1]
                perm[-1] = perm[-2]
                perm[-2] = tmp
                ctx.insert_new_node_on_input(node, "Transpose", node.input[1], perm=perm)

        unsupported = ["a_is_sparse", "b_is_sparse"]
        for i in unsupported:
            val = node.get_attr(i)
            if val is not None and val.i != 0:
                raise ValueError(node.type + " attribute " + i + " is not supported")


@tf_op("Erf")
class Erf:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        """Error function."""
        # constant names
        a1 = "erf_a1"
        a2 = "erf_a2"
        a3 = "erf_a3"
        a4 = "erf_a4"
        a5 = "erf_a5"
        p = "erf_p"
        one = "erf_one"
        null = "erf_null"

        n = node.name
        output_name = node.output[0]
        erf_a1_node = ctx.get_node_by_output("erf_a1")
        if erf_a1_node is None:
            # insert the constants for erf once
            ctx.make_const(a1, np.array(0.254829592, dtype=np.float32))
            ctx.make_const(a2, np.array(-0.284496736, dtype=np.float32))
            ctx.make_const(a3, np.array(1.421413741, dtype=np.float32))
            ctx.make_const(a4, np.array(-1.453152027, dtype=np.float32))
            ctx.make_const(a5, np.array(1.061405429, dtype=np.float32))
            ctx.make_const(p, np.array(0.3275911, dtype=np.float32))
            ctx.make_const(one, np.array(1., dtype=np.float32))
            ctx.make_const(null, np.array(0., dtype=np.float32))

        x = node.input[0]

        # erf(x):
        #  sign = 1 if x >= 0 else -1
        #  x = abs(x)
        #  # A&S formula 7.1.26
        #  t = 1.0 / (1.0 + p * x)
        #  y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) *  t * math.exp(-x * x)
        #  return sign * y  # erf(-x) = -erf(x)

        x_node = ctx.make_node("Abs", [x], op_name_scope=node.name, name="x")
        negx_node = ctx.make_node("Sub", [null, x], op_name_scope=node.name, name="negx")
        is_positive_node = ctx.make_node("Greater", [x, null], op_name_scope=node.name, name="isPositive")
        is_positive_value_node = ctx.make_node("Cast", is_positive_node.output, op_name_scope=node.name,
                                               name="isPositiveValue", attr={"to": onnx_pb.TensorProto.FLOAT})
        is_neg_node = ctx.make_node("Less", [x, null], op_name_scope=node.name, name="isNeg")
        ig_neg_value_node = ctx.make_node("Cast", is_neg_node.output, op_name_scope=node.name, name="isNegValue",
                                          attr={"to": onnx_pb.TensorProto.FLOAT})
        sign0_node = ctx.make_node("Sub", [is_positive_value_node.output[0], ig_neg_value_node.output[0]],
                                   op_name_scope=node.name, name="sign0")
        sign_add_one_node = ctx.make_node("Add", [sign0_node.output[0], one], op_name_scope=node.name,
                                          name="signAddOne")
        non_zero_node = ctx.make_node("Abs", sign0_node.output, op_name_scope=node.name, name="nonZero")
        sign_node = ctx.make_node("Sub", [sign_add_one_node.output[0], non_zero_node.output[0]],
                                  op_name_scope=node.name, name="sign")
        num_4_node = ctx.make_node("Mul", [x_node.output[0], p], op_name_scope=node.name, name="4")
        num_5_node = ctx.make_node("Add", [num_4_node.output[0], one], op_name_scope=node.name, name="5")
        t_node = ctx.make_node("Div", [one, num_5_node.output[0]], op_name_scope=node.name, name="t")
        xsq_node = ctx.make_node("Mul", [x, negx_node.output[0]], op_name_scope=node.name, name="xsq")
        num_6_node = ctx.make_node("Exp", xsq_node.output, op_name_scope=node.name, name="6")
        num_7_node = ctx.make_node("Mul", [num_6_node.output[0], t_node.output[0]], op_name_scope=node.name, name="7")
        num_8_node = ctx.make_node("Mul", [t_node.output[0], a5], op_name_scope=node.name, name="8")
        num_9_node = ctx.make_node("Add", [num_8_node.output[0], a4], op_name_scope=node.name, name="9")
        num_10_node = ctx.make_node("Mul", [num_9_node.output[0], t_node.output[0]], op_name_scope=node.name, name="10")
        num_11_node = ctx.make_node("Add", [num_10_node.output[0], a3], op_name_scope=node.name, name="11")
        num_12_node = ctx.make_node("Mul", [num_11_node.output[0], t_node.output[0]], op_name_scope=node.name,
                                    name="12")
        num_13_node = ctx.make_node("Add", [num_12_node.output[0], a2], op_name_scope=node.name, name="13")
        num_14_node = ctx.make_node("Mul", [num_13_node.output[0], t_node.output[0]], op_name_scope=node.name,
                                    name="14")
        num_15_node = ctx.make_node("Add", [num_14_node.output[0], a1], op_name_scope=node.name, name="15")
        num_16_node = ctx.make_node("Mul", [num_15_node.output[0], num_7_node.output[0]], op_name_scope=node.name,
                                    name="16")
        num_17_node = ctx.make_node("Sub", [one, num_16_node.output[0]], op_name_scope=node.name, name="17")

        shapes = node.output_shapes
        dtypes = node.output_dtypes
        ctx.remove_node(node.name)
        ctx.make_node("Mul", [num_17_node.output[0], sign_node.output[0]], outputs=[output_name], name=n,
                      shapes=shapes, dtypes=dtypes)

    @classmethod
    def version_9(cls, ctx, node, **kwargs):
        pass


@tf_op("FloorDiv")
class FloorDiv:
    @classmethod
    def version_6(cls, ctx, node, **kwargs):
        # T output = FloorDiv(T x, T y)
        node.type = "Div"
        dtype = ctx.get_dtype(node.input[0])
        if dtype in [onnx_pb.TensorProto.FLOAT, onnx_pb.TensorProto.FLOAT16, onnx_pb.TensorProto.DOUBLE]:
            new_node_name = utils.make_name("floor_div_res")
            floor_res = ctx.insert_new_node_on_output(op_type="Floor", output_name=node.output[0],
                                                      name=new_node_name)
            ctx.copy_dtype(node.output[0], floor_res.output[0])
            ctx.copy_shape(node.output[0], floor_res.output[0])


@tf_op("FloorMod")
class FloorMod:
    @classmethod
    def version_7(cls, ctx, node, **kwargs):
        # T output = FloorMod(T x, T y)
        div = ctx.make_node(op_type="Div", inputs=node.input)
        dtype = ctx.get_dtype(node.input[0])
        if dtype in [onnx_pb.TensorProto.FLOAT, onnx_pb.TensorProto.FLOAT16, onnx_pb.TensorProto.DOUBLE]:
            div = ctx.make_node(op_type="Floor", inputs=div.output)

        mul = ctx.make_node(op_type="Mul", inputs=[div.output[0], node.input[1]])
        # res node will take over shape&dtype&output connection info of original "node"
        shapes = node.output_shapes
        dtypes = node.output_dtypes
        ctx.remove_node(node.name)
        ctx.make_node(op_type="Sub", inputs=[node.input[0], mul.output[0]],
                      name=node.name, outputs=node.output, shapes=shapes, dtypes=dtypes)


@tf_op("Selu")
class Selu:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        pass


@tf_op("Cumsum", onnx_op="CumSum")
class CumSum:
    @classmethod
    def version_11(cls, ctx, node, **kwargs):
        pass


@tf_op("Round")
class Round:
    @classmethod
    def version_11(cls, ctx, node, **kwargs):
        pass


@tf_op("MatrixDeterminant", onnx_op="Det")
class Det:
    @classmethod
    def version_11(cls, ctx, node, **kwargs):
        pass


@tf_op(["LeftShift", "RightShift"])
class BitShift:

    @classmethod
    def version_11(cls, ctx, node, **kwargs):
        dir_map = {"LeftShift": "LEFT", "RightShift": "RIGHT"}
        direction = dir_map[node.type]
        supported = [onnx_pb.TensorProto.UINT8, onnx_pb.TensorProto.UINT16,
                     onnx_pb.TensorProto.UINT32, onnx_pb.TensorProto.UINT64]
        type_map = {onnx_pb.TensorProto.INT8: onnx_pb.TensorProto.UINT8,
                    onnx_pb.TensorProto.INT16: onnx_pb.TensorProto.UINT32,
                    onnx_pb.TensorProto.INT32: onnx_pb.TensorProto.UINT64}
        shapes = node.output_shapes
        dtypes = node.output_dtypes
        ctx.remove_node(node.name)

        node = ctx.make_node("BitShift", inputs=node.input, outputs=node.output, name=node.name,
                             shapes=shapes, dtypes=dtypes, domain=constants.ONNX_DOMAIN, attr={'direction': direction})

        if node.maybe_cast_input([supported, supported], type_map):
            cast_back_node = ctx.insert_new_node_on_output("Cast", node.output[0],
                                                           name=utils.make_name(node.name) + "_castback")
            cast_back_node.set_attr("to", dtypes[0])
            ctx.set_dtype(cast_back_node.output[0], dtypes[0])
            ctx.copy_shape(node.name, cast_back_node.output[0])


@tf_op("SquaredDistance", onnx_op="MeanSquaredDistance")
class SquaredDistance:
    @classmethod
    def version_12(cls, ctx, node, **kwargs):
        node.attr["reduction"] = "none"


@tf_op("Einsum")
class Einsum:
    @classmethod
    def version_12(cls, ctx, node, **kwargs):
        del node.attr["N"]


@tf_op("IsFinite")
class IsFinite:
    @classmethod
    def version_10(cls, ctx, node, **kwargs):
        # map to onnx as:
        # not (isinf(x) or isnan(x))

        shapes = node.output_shapes
        dtypes = [onnx_pb.TensorProto.BOOL] * len(node.output_dtypes)

        ctx.remove_node(node.name)

        inf_node = ctx.make_node("IsInf", inputs=node.input, name=utils.make_name(node.name),
                                 shapes=shapes, dtypes=dtypes)
        nan_node = ctx.make_node("IsNaN", inputs=node.input, name=utils.make_name(node.name),
                                 shapes=shapes, dtypes=dtypes)
        or_node = ctx.make_node("Or", inputs=[inf_node.output[0], nan_node.output[0]], name=utils.make_name(node.name),
                                shapes=shapes, dtypes=dtypes)
        _ = ctx.make_node("Not", inputs=or_node.output, name=node.name,
                          shapes=shapes, dtypes=dtypes)


@tf_op("Atan2")
class Atan2Op:
    # support more dtype
    supported_dtypes = [
        onnx_pb.TensorProto.FLOAT,
        onnx_pb.TensorProto.FLOAT16,
        onnx_pb.TensorProto.DOUBLE
    ]

    @classmethod
    def version_9(cls, ctx, node, **kwargs):
        """
        Obtained with a linear regression.

        ::

            def atan2(y, x):
                sx = numpy.sign(x)
                sy = numpy.sign(y)
                pi_part = (sy + sx * (sy ** 2 - 1)) * (sx - 1) * (-numpy.pi/2)
                atan_part = numpy.arctan(y / (x + (1 - sx ** 2))) * sx ** 2
                return atan_part + pi_part
        """

        onnx_dtype = ctx.get_dtype(node.input[0])
        shape = ctx.get_shape(node.input[0])
        np_dtype = utils.map_onnx_to_numpy_type(onnx_dtype)

        # sign part

        sign_x_node = ctx.make_node(
            "Sign", inputs=node.input[1:],
            name=utils.make_name(node.name + 'signx'))
        sign_y_node = ctx.make_node(
            "Sign", inputs=node.input[:1],
            name=utils.make_name(node.name + 'signy'))

        sx_node = ctx.make_node(
            "Cast", sign_x_node.output[:1], attr={"to": onnx_dtype},
            name=utils.make_name(node.name + 'csignx'))
        sy_node = ctx.make_node(
            "Cast", sign_y_node.output[:1], attr={"to": onnx_dtype},
            name=utils.make_name(node.name + 'csigny'))

        # cst

        one_node = ctx.make_const(
            utils.make_name("{}_one".format(node.name)),
            np.array([1], dtype=np_dtype))

        pib2_node = ctx.make_const(
            utils.make_name("{}_pi".format(node.name)),
            np.array(- np.pi / 2, dtype=np_dtype))

        # pi_part = (sy + sx * (sy ** 2 - 1)) * (sx - 1) * (-numpy.pi/2)

        sxm1_node = ctx.make_node(
            "Sub", [sx_node.output[0], one_node.output[0]],
            name=utils.make_name(node.name + 'sxm1'))
        sy2_node = ctx.make_node(
            "Mul", [sy_node.output[0], sy_node.output[0]],
            name=utils.make_name(node.name + 'sy2'))
        sy2m1_node = ctx.make_node(
            "Sub", [sy2_node.output[0], one_node.output[0]],
            name=utils.make_name(node.name + 'sy2m1'))
        sxsy2m1_node = ctx.make_node(
            "Mul", [sx_node.output[0], sy2m1_node.output[0]],
            name=utils.make_name(node.name + 'sxsy2m1'))
        sysxsy2m1_node = ctx.make_node(
            "Add", [sy_node.output[0], sxsy2m1_node.output[0]],
            name=utils.make_name(node.name + 'sysxsy2m1'))
        m1_node = ctx.make_node(
            "Mul", [sysxsy2m1_node.output[0], sxm1_node.output[0]],
            name=utils.make_name(node.name + 'm1'))
        pi_part = ctx.make_node(
            "Mul", [m1_node.output[0], pib2_node.output[0]],
            name=utils.make_name(node.name + 'pip'))

        # atan

        sx2_node = ctx.make_node(
            "Mul", [sx_node.output[0], sx_node.output[0]],
            name=utils.make_name(node.name + 'sx2'))
        sx2m1_node = ctx.make_node(
            "Sub", [sx2_node.output[0], one_node.output[0]],
            name=utils.make_name(node.name + 'sx2m1'))
        xsx2m1_node = ctx.make_node(
            "Add", [node.input[1], sx2m1_node.output[0]],
            name=utils.make_name(node.name + 'xsx2m1'))
        div_node = ctx.make_node(
            "Div", inputs=[node.input[0], xsx2m1_node.output[0]],
            name=utils.make_name(node.name + 'div'))
        atan0_node = ctx.make_node(
            "Atan", inputs=[div_node.output[0]],
            name=utils.make_name(node.name + 'atan0'))
        atan_node = ctx.make_node(
            "Mul", inputs=[sx2_node.output[0], atan0_node.output[0]],
            name=utils.make_name(node.name + 'atan'))

        # final

        ctx.remove_node(node.name)

        last_node = ctx.make_node(
            "Add", inputs=[atan_node.output[0], pi_part.output[0]],
            op_name_scope=node.name + 'all',
            shapes=[shape], dtypes=[onnx_dtype])
        ctx.replace_all_inputs(ctx.get_nodes(), node.output[0], last_node.output[0])
