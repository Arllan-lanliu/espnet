import pytest
import torch

from espnet2.tts.transformer import Transformer


@pytest.mark.parametrize("eprenet_conv_layers", [0, 1])
@pytest.mark.parametrize("dprenet_layers", [0, 1])
@pytest.mark.parametrize("postnet_layers", [0, 1])
@pytest.mark.parametrize(
    "positionwise_layer_type", ["linear", "conv1d", "conv1d-linear"]
)
@pytest.mark.parametrize("normalize_before", [True, False])
@pytest.mark.parametrize("concat_after", [True, False])
@pytest.mark.parametrize("reduction_factor", [1, 2, 3])
@pytest.mark.parametrize("spk_embed_dim", [None, 2])
@pytest.mark.parametrize("spk_embed_integration_type", ["add", "concat"])
@pytest.mark.parametrize("loss_type", ["L1+L2", "L1"])
@pytest.mark.parametrize("use_guided_attn_loss", [True, False])
@pytest.mark.parametrize(
    "modules_applied_guided_attn", [["encoder", "decoder", "encoder-decoder"]]
)
def test_tranformer(
    eprenet_conv_layers,
    dprenet_layers,
    postnet_layers,
    positionwise_layer_type,
    normalize_before,
    concat_after,
    reduction_factor,
    spk_embed_dim,
    spk_embed_integration_type,
    loss_type,
    use_guided_attn_loss,
    modules_applied_guided_attn,
):
    model = Transformer(
        idim=10,
        odim=5,
        embed_dim=4,
        eprenet_conv_layers=eprenet_conv_layers,
        eprenet_conv_filts=5,
        dprenet_layers=dprenet_layers,
        dprenet_units=4,
        elayers=1,
        eunits=4,
        adim=4,
        aheads=2,
        dlayers=1,
        dunits=4,
        postnet_layers=postnet_layers,
        postnet_chans=4,
        postnet_filts=5,
        positionwise_layer_type=positionwise_layer_type,
        positionwise_conv_kernel_size=1,
        use_scaled_pos_enc=True,
        use_batch_norm=True,
        encoder_normalize_before=normalize_before,
        decoder_normalize_before=normalize_before,
        encoder_concat_after=concat_after,
        decoder_concat_after=concat_after,
        reduction_factor=reduction_factor,
        spk_embed_dim=spk_embed_dim,
        spk_embed_integration_type=spk_embed_integration_type,
        loss_type=loss_type,
        use_guided_attn_loss=use_guided_attn_loss,
        modules_applied_guided_attn=modules_applied_guided_attn,
    )

    inputs = dict(
        text=torch.randint(0, 10, (2, 4)),
        text_lengths=torch.tensor([4, 1], dtype=torch.long),
        speech=torch.randn(2, 3, 5),
        speech_lengths=torch.tensor([3, 1], dtype=torch.long),
    )
    if spk_embed_dim is not None:
        inputs.update(spembs=torch.randn(2, spk_embed_dim))
    loss, *_ = model(**inputs)
    loss.backward()

    with torch.no_grad():
        model.eval()
        inputs = dict(
            text=torch.randint(0, 10, (2,)),
        )
        if spk_embed_dim is not None:
            inputs.update(spembs=torch.randn(spk_embed_dim))
        model.inference(**inputs, maxlenratio=1.0)
