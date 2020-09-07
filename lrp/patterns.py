import torch
import torch.nn.functional as F

from tqdm import tqdm

def safe_divide(a, b):
    return a / (b + (b==0).float())

def prod(module, input, output, mask_fn):
    mask = mask_fn(output).float()
    output_ = output * mask

    if isinstance(module, torch.nn.Linear):
        return input.t() @ mask, output, input.t() @ output_, module.weight, lambda w: w.t()

    elif isinstance(module, torch.nn.Conv2d): 

        p1, p2 = module.padding
        s1, s2 = module.stride
        k1, k2 = module.kernel_size

        # Eprint("Pad shape: ", F.pad(input, (p2, p2, p1, p1)).shape, input.shape, p1, p2)
        input = F.pad(input, (p1, p1, p2, p2)).unfold(2, k1, s1).unfold(3, k2, s2)
        _, c, h, w, *_ = input.shape # [bs, c, h, w, kh, kw]

        input = input.permute(1, 4, 5, 0, 2, 3).contiguous() 
        input = input.view( c * k1 * k2, -1) # [ c*kh*kw, bs*h*w ]

        def reshape_output(o):
            o = o.permute(0, 2, 3, 1).contiguous()
            return o.view(-1, module.out_channels)

        # [ bs, c_out, h, w ]
        # output_ = output_.permute(0, 2, 3, 1).contiguous()
        # [ bs*h*w, c_out ]
        # output = output.view(-1, module.out_channels)
        output_ = reshape_output(output_)
        output  = reshape_output(output)
        mask    = reshape_output(mask)
        return input @ mask, output, input @ output_, module.weight.view(module.out_channels, -1), lambda w: w.view(module.weight.shape)
    else:
        raise NotImplmentedError()

def _fit_pattern(model, train_loader, max_iter, mask_fn = lambda y: torch.ones_like(y)):
    stats_in    = [] 
    stats_out   = []
    stats_prod  = []
    weights     = []
    cnt         = 0
    cnt_all     = 0

    first = True
    for b, (x, _) in enumerate(tqdm(train_loader)): 

        i = 0
        # with torch.no_grad():
        for m in model:
            y = m(x)
            if not (isinstance(m, torch.nn.Linear) or isinstance(m, torch.nn.Conv2d)): 
                x = y.clone()
                continue
            
            x_, y_, xy_prod, w, w_fn = prod(m, x, y, mask_fn)

            if first:
                stats_in.append(x_)
                stats_out.append(y_.sum(0)) # Use all y
                stats_prod.append(xy_prod)
                weights.append((w, w_fn))
            else:
                stats_in[i]     += x_
                stats_out[i]    += y_.sum(0)
                stats_prod[i]   += xy_prod

            x = y.clone()
            cnt += x_.size(0)
            cnt_all += x.size(0)
            i += 1
            
        first =False
        if max_iter is not None and b == max_iter: break

    mu_x =  [x  / cnt     for x  in stats_in]
    mu_y =  [y  / cnt_all for y  in stats_out]
    mu_xy = [xy / cnt     for xy in stats_prod]

    def pattern(x_, y_, xy_, W2d):
        # W2d: [out, in]
        W, w_fn = W2d
        # ExEy = x_.view(-1, 1) * y_.view(1, -1)
        ExEy = x_ * y_
        
        cov_xy = xy_ - ExEy # [in, out]
        w_cov_xy = torch.diag(W @ cov_xy) # [out,]
        A = safe_divide(cov_xy, w_cov_xy[None, :])
        A = w_fn(A) # Reshape to original kernel size

        return A

    patterns = [pattern(*vars) for vars in zip(mu_x, mu_y, mu_xy, weights)]
    return patterns



@torch.no_grad()
def fit_patternnet(model, train_loader, max_iter=None):
    return _fit_pattern(model, train_loader, max_iter)

@torch.no_grad()
def fit_patternnet_positive(model, train_loader, max_iter=None):
    return _fit_pattern(model, train_loader, max_iter, lambda y: y >= 0)


