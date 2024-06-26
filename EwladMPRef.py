import torch
from torch_scatter import scatter

from ocpmodels.models.gemnet.layers.base_layers import Dense, ResidualLayer
from ocpmodels.modules.scaling.scale_factor import ScaleFactor


class EwaldBlock(torch.nn.Module):
    """
    Long-range block from the Ewald message passing method

    Parameters
    ----------
        shared_downprojection: Dense,
            Downprojection block in Ewald block update function,
            shared between subsequent Ewald Blocks.
        emb_size_atom: int
            Embedding size of the atoms.
        downprojection_size: int
            Dimension of the downprojection bottleneck
        num_hidden: int
            Number of residual blocks in Ewald block update function.
        activation: callable/str
            Name of the activation function to use in the dense layers.
        scale_file: str
            Path to the json file containing the scaling factors.
        name: str
            String identifier for use in scaling file.
        use_pbc: bool
            Set to True if periodic boundary conditions are applied.
        delta_k: float
            Structure factor voxel resolution
            (only relevant if use_pbc == False).
        k_rbf_values: torch.Tensor
            Pre-evaluated values of Fourier space RBF
            (only relevant if use_pbc == False).
        return_k_params: bool = True,
            Whether to return k,x dot product and damping function values.
    """

    def __init__(
        """
        ACT: This method initializes the Ewlad Block with several parameters, including the shared downprojection, embedding size of the atoms, downprojection size, number of hidden layers, activation functions etc. 
        It also sets up the necessary layers for the Ewald block, such as the downprojection (self.down), upprojection (self.up), pre-residual layer (self.pre_presidual), and the Ewald layers (self.ewald_layers).
        
        Q) Downprojection: In the context of NN, down and upprojection refer to the process of transforming data to a lower or higher dimensional space respectively. The self.down is used to transform the input data to
        a lower dimensional space. This is often done to reduce the complexity of the models and catpture the most important features of the data. The self.down is a dense layer that transforms the input data to a lower-dimensional
        space. The transformation is carried out through a matrix multiplication followed by an activation function. The matrix (or weights of the dense layer) is learned during the training process. The size of this matrix 
        determines the dimensionality of the output, which is the downprojection_size in this case. 

        Q) Upprojection: The self.up is another dense layer that transforms the lower-dimensional data back to its original high-dimensional space. Similar to the downprojection, the transformation is carried out 
        through a matrix multiplication followed by an activation function. The size of this matrix determines the dimensionality of the output, which is the emb_size_atom in this case.

        Q) Pre-residual layer (self.pre_residual): This is an instance of the ResidualLayer class. Residual layers are a key component of Residual Networks (ResNets), which are a type of neural network architecture that 
        introduces “skip connections” or “shortcuts” to allow the gradient to be directly backpropagated to earlier layers1. The pre-residual layer in this code is applied to the atomic embeddings before they are passed 
        through the Ewald layers.
        
        Q) Ewald layers (self.ewald_layers): These are the layers that implement the Ewald message passing method. They are created in the get_mlp method and consist of a dense layer followed by several residual layers. 
        The Ewald layers are applied to the atomic embeddings after they have been processed by the pre-residual layer and the Fourier space filter. The purpose of these layers is to update the atomic embeddings 
        based on the long-range interactions captured by the Ewald message passing method.
        
        In the context of this code, both the pre-residual layer and the Ewald layers are used to update the atomic embeddings (h) during the forward pass of the Ewald block. 
        The updated embeddings are then returned by the forward method

        """        
        self,
        shared_downprojection: Dense,
        emb_size_atom: int,
        downprojection_size: int,
        num_hidden: int,
        activation=None,
        name=None,  # identifier in case a ScalingFactor is applied to Ewald output
        use_pbc: bool = True,
        delta_k: float = None,
        k_rbf_values: torch.Tensor = None,
        return_k_params: bool = True,
    ):
        super().__init__()
        self.use_pbc = use_pbc
        self.return_k_params = return_k_params

        self.delta_k = delta_k
        self.k_rbf_values = k_rbf_values

        self.down = shared_downprojection
        self.up = Dense(
            downprojection_size, emb_size_atom, activation=None, bias=False
        )
        self.pre_residual = ResidualLayer(
            emb_size_atom, nLayers=2, activation=activation
        )
        self.ewald_layers = self.get_mlp(
            emb_size_atom, emb_size_atom, num_hidden, activation
        )
        if name is not None:
            self.ewald_scale_sum = ScaleFactor(name + "_sum")
        else:
            self.ewald_scale_sum = None

    def get_mlp(self, units_in, units, num_hidden, activation):
    """

    ACT: This method creates a multi-layer perceptron (MLP) with a specified number of input units, output units, and hidden layers. 
    The MLP consists of a dense layer followed by several residual layers. 
    The step-by-step explaination of the method is as follows.

    """
        dense1 = Dense(units_in, units, activation=activation, bias=False)                 # Dense Layer Creation (dense1): The method starts by creating a dense (fully connected) layer with units_in input units, 
         # units output units, and the specified activation function. The bias parameter is set to False, meaning that no bias term is added in this layer.
        
        mlp = [dense1] # MLP Initialization (mlp): The dense layer is added to the MLP, which is initialized as a list containing just this layer.
        
        res = [
            ResidualLayer(units, nLayers=2, activation=activation)
            for i in range(num_hidden)
        ]   #                             Residual Layers Creation (res): A list of residual layers is created. The number of residual layers is specified by num_hidden. Each residual layer has units 
        # input and output units, 2 layers (nLayers=2), and uses the specified activation function.
        
        mlp += res # The residual layers are added to the MLP.
        
        return torch.nn.ModuleList(mlp) # The MLP, now a list of layers (a dense layer followed by several residual layers), is converted to a torch.nn.ModuleList and returned. 
        # The torch.nn.ModuleList is a container class in PyTorch that holds submodules in a list. 
        # It allows you to have full control over the order of layers, and it properly registers the layers as submodules of the model so that they are recognized by methods like .to(device), .train(), .eval(), etc.

    def forward(
        self,
        h: torch.Tensor,
        x: torch.Tensor,
        k: torch.Tensor,
        num_batch: int,
        batch_seg: torch.Tensor,
        # Dot products k^Tx and damping values: need to be computed only once per structure
        # Ewald block in first interaction block gets None as input, therefore computes these
        # values and then passes them on to Ewald blocks in later interaction blocks
        dot: torch.Tensor = None,
        sinc_damping: torch.Tensor = None,
    ):
        hres = self.pre_residual(h) # Pre-residual layer application (hres = self.pre_residual(h)): The pre-residual layer is applied to the atomic embeddings h, resulting in hres.
        # Compute dot products and damping values if not already done so by an Ewald block 
        # in a previous interaction block
        if dot == None:  # Calculate the dot product only if it is not previously computed. Computed only once in the first call. 
            b = batch_seg.view(-1, 1, 1).expand(-1, k.shape[-2], k.shape[-1]) # The batch segmentation is expanded and reshaped to match the shape of k-vectors. 
            dot = torch.sum(torch.gather(k, 0, b) * x.unsqueeze(-2), dim=-1) # The batch_seg is used to gather the relevant elements of k, which are then multiplied by cooordinates x. The dot product is computed with torch.sum. This whole thing basically evaluates 
            # the dot product between positions x and k-vectors k. 
        if sinc_damping == None: # Sinc damping as mentioned in the appendix of the EwaldMP paper. Only becomes relevant for aperiodic cases. Used for damping the contributions due of high wavevector components in the Fourier space.   
            if self.use_pbc == False: # # If pbc = False
                sinc_damping = (
                    torch.sinc(0.5 * self.delta_k * x[:, 0].unsqueeze(-1))
                    * torch.sinc(0.5 * self.delta_k * x[:, 1].unsqueeze(-1))
                    * torch.sinc(0.5 * self.delta_k * x[:, 2].unsqueeze(-1))
                )
                sinc_damping = sinc_damping.expand(-1, k.shape[-2])
            else:
                sinc_damping = 1 # Else it is 1. 

        # Compute Fourier space filter from weights
        if self.use_pbc: # If PBC being used. 
            self.kfilter = (  
                torch.matmul(self.up.linear.weight, self.down.linear.weight)  # The Fourier space filter is computed via a matrix multiplication of the weights from the upprojection and downprojection layer. 
                .T.unsqueeze(0) 
                .expand(num_batch, -1, -1) # The result is transposed, unsqueezed to add an extra dimension at index (0), and expanded to match the number of batches. 
            )
        else: # This is if PBC = False 
            self.k_rbf_values = self.k_rbf_values.to(x.device) # Move the pre-evaluated values of radial basis functions on the same (CPU or GPU device)
            self.kfilter = (
                self.up(self.down(self.k_rbf_values))
                .unsqueeze(0)
                .expand(num_batch, -1, -1)
            ) # compute k-space filter. 

        # Compute real and imaginary parts of structure factor
        sf_real = hres.new_zeros(
            num_batch, dot.shape[-1], hres.shape[-1]  # This initializes the zero tensors with the same dtype and device as the hres (the atomic embeddings after the pre-residual layer). 
        ).index_add_(  # The index.adds the cosine part at the indices specified by batch_seg. The operation happens in-place. 
            0,
            batch_seg,
            hres.unsqueeze(-2).expand(-1, dot.shape[-1], -1)
            * (sinc_damping * torch.cos(dot))     # Computing cosine part of the structure factor (Real part)
            .unsqueeze(-1)
            .expand(-1, -1, hres.shape[-1]), # Reshaped and expanded to match the shape of hres. 
        )  
        sf_imag = hres.new_zeros(   # Same as in the above step
            num_batch, dot.shape[-1], hres.shape[-1] 
        ).index_add_(
            0,
            batch_seg,
            hres.unsqueeze(-2).expand(-1, dot.shape[-1], -1)
            * (sinc_damping * torch.sin(dot))  # Computing the imaginary Sine part of the structure factor. 
            .unsqueeze(-1)
            .expand(-1, -1, hres.shape[-1]), # Reshaped and expanded to match the shape of hres. 
        )

        # Apply Fourier space filter; scatter back to position space
        h_update = 0.01 * torch.sum(
            torch.index_select(sf_real * self.kfilter, 0, batch_seg) # The Fourier space filter is applied to the real part of the Sk (Structure Factor). 
            # The index_select selects elements from the filtered Sk along the first dimension using the indices specified in batch_seg. 
            * (sinc_damping * torch.cos(dot)) # The selected elements are then multiplied by the product of sinc_damping and cosine of the the dot (k.x)  
            .unsqueeze(-1)
            .expand(-1, -1, hres.shape[-1]) # Reshaped to match hres
            + torch.index_select(sf_imag * self.kfilter, 0, batch_seg) # The Fourier space filter is applied to the imag. part of the Sk. Rest is same as above. 
            * (sinc_damping * torch.sin(dot))
            .unsqueeze(-1)
            .expand(-1, -1, hres.shape[-1]),
            dim=1,
        ) # The torch.sum operation effectively scatters the filtered Sk back to position space. The results are scaled by 0.01 (why?) ? 

        if self.ewald_scale_sum is not None:
            h_update = self.ewald_scale_sum(h_update, ref=h)

        # Apply update function
        for layer in self.ewald_layers:
            h_update = layer(h_update)

        if self.return_k_params:
            return h_update, dot, sinc_damping
        else:
            return h_update


# Atom-to-atom continuous-filter convolution
class HadamardBlock(torch.nn.Module):
    """
    Aggregate atom-to-atom messages by Hadamard (i.e., component-wise)
    product of embeddings and radial basis functions

    Parameters
    ----------
        emb_size_atom: int
            Embedding size of the atoms.
        emb_size_atom: int
            Embedding size of the edges.
        nHidden: int
            Number of residual blocks.
        activation: callable/str
            Name of the activation function to use in the dense layers.
        scale_file: str
            Path to the json file containing the scaling factors.
        name: str
            String identifier for use in scaling file.
    """

    def __init__(
        self,
        emb_size_atom: int,
        emb_size_bf: int,
        nHidden: int,
        activation=None,
        scale_file=None,
        name: str = "hadamard_atom_update",
    ):
        super().__init__()
        self.name = name

        self.dense_bf = Dense(
            emb_size_bf, emb_size_atom, activation=None, bias=False
        )
        self.scale_sum = ScalingFactor(
            scale_file=scale_file, name=name + "_sum"
        )
        self.pre_residual = ResidualLayer(
            emb_size_atom, nLayers=2, activation=activation
        )
        self.layers = self.get_mlp(
            emb_size_atom, emb_size_atom, nHidden, activation
        )

    def get_mlp(self, units_in, units, nHidden, activation):
        dense1 = Dense(units_in, units, activation=activation, bias=False)
        mlp = [dense1]
        res = [
            ResidualLayer(units, nLayers=2, activation=activation)
            for i in range(nHidden)
        ]
        mlp += res
        return torch.nn.ModuleList(mlp)

    def forward(self, h, bf, idx_s, idx_t):
        """
        Returns
        -------
            h: torch.Tensor, shape=(nAtoms, emb_size_atom)
                Atom embedding.
        """
        nAtoms = h.shape[0]
        h_res = self.pre_residual(h)

        mlp_bf = self.dense_bf(bf)

        x = torch.index_select(h_res, 0, idx_s) * mlp_bf

        x2 = scatter(x, idx_t, dim=0, dim_size=nAtoms, reduce="sum")
        # (nAtoms, emb_size_edge)
        x = self.scale_sum(h, x2)

        for layer in self.layers:
            x = layer(x)  # (nAtoms, emb_size_atom)

        return x
