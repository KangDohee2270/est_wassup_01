import torch
from torch import nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
import torchmetrics
from torch.optim.lr_scheduler import StepLR
from dataclasses import dataclass, field
from typing import Type, Optional
import pandas as pd
import wandb
from sklearn.model_selection import KFold
import numpy as np

def evaluate(
  model:nn.Module,
  data_loader:DataLoader,
  metric:torchmetrics.metric.Metric,
  device:str='cpu',
) -> None:
  '''evaluate
  
  Args:
      model: model
      criterions: list of criterion functions
      data_loader: data loader
      device: device
      metrcis: metrics
  '''
  model.eval()
  with torch.inference_mode():
    for X, y in data_loader:
      X, y = X.to(device), y.to(device)
      output = model(X)
      metric.update(output, y)

def ml_kfold(Model, x_train: np.array, y_train: np.array, model_params: tuple):
  """ K-Fold function for ML model

    Args:
      Model : ML model from sklearn(ex. sklearn.ensemble.RandomForestRegressor)
      x_train (np.array): features used for training. It must be of type np.array 
      y_train (np.array): target used for training. It must be of type np.array 
      model_params (tuple): model parameters

    Return:
      mean absolute error for train, validation set
  """
  from sklearn.metrics import mean_absolute_error
  models = [Model(**model_params) for _ in range(5)]
  metrics = {'trn_mae': [], 'val_mae': []} # loss_list to metrics

  kf = KFold(n_splits = 5, shuffle = True, random_state = 1111)
  ##하나의 고정된 validation set을 사용한다면 해당 성능이 일반적으로 좋은지 운으로 좋았던건지 판단할 수 있음
  # print(y_train.shape)

  for i, (trn_idx, val_idx) in enumerate(kf.split(x_train)):
    model= models[i]
    X_trn, y_trn = x_train[trn_idx], y_train[trn_idx]
    X_val, y_val = x_train[val_idx], y_train[val_idx]
    model.fit(X_trn, y_trn)
    y_pred_trn = model.predict(X_trn)
    y_pred_val = model.predict(X_val)
    trn_mae = mean_absolute_error(y_pred_trn, y_trn)
    val_mae = mean_absolute_error(y_pred_val, y_val)
    print(f"Fold {i+1} Done.")
    print(f"Loss: Train: {trn_mae:.6f}, Val: {val_mae:.6f}")

    metrics['trn_mae'].append(trn_mae)
    metrics['val_mae'].append(val_mae)
    
  return pd.DataFrame(metrics)

@dataclass
class KFoldCV:
  X: torch.Tensor
  y: torch.Tensor
  Model: Type[nn.Module]
  model_args: tuple = tuple()
  model_kwargs: dict = field(default_factory=lambda : {})
  epochs: int = 500
  criterion: callable = F.mse_loss
  Optimizer: Type[torch.optim.Optimizer] = torch.optim.Adam
  optim_kwargs: dict = field(default_factory=lambda : {})
  trn_dl_kwargs: dict = field(default_factory=lambda : {'batch_size': 36})
  val_dl_kwargs: dict = field(default_factory=lambda : {'batch_size': 36})
  n_splits: int = 5
  metric: torchmetrics.Metric = torchmetrics.MeanSquaredError(squared=False)
  device: str = 'cpu'

  def run(self):
    from torch.utils.data import TensorDataset
    from sklearn.model_selection import KFold
    from tqdm.auto import trange
    from train import train_one_epoch

    model = self.Model(*self.model_args, **self.model_kwargs).to(self.device)
    models = [self.Model(*self.model_args, **self.model_kwargs).to(self.device) for _ in range(self.n_splits)]
    for m in models:
      m.load_state_dict(model.state_dict())
    kfold = KFold(n_splits=self.n_splits, shuffle=False)

    metrics = {'trn_mae': [], 'val_mae': []}
    for i, (trn_idx, val_idx) in enumerate(kfold.split(self.X)):
      X_trn, y_trn = self.X[trn_idx], self.y[trn_idx]
      X_val, y_val = self.X[val_idx], self.y[val_idx]
      ds_trn = TensorDataset(X_trn, y_trn)
      ds_val = TensorDataset(X_val, y_val)

      dl_trn = DataLoader(ds_trn, **self.trn_dl_kwargs)
      dl_val = DataLoader(ds_val, **self.val_dl_kwargs)

      m = models[i]
      optim = self.Optimizer(m.parameters(), **self.optim_kwargs)

      pbar = trange(self.epochs)
      for _ in pbar:
        for train_loss in train_one_epoch(m, self.criterion, optim, dl_trn, self.metric, self.device, save_ratio=50):
          if wandb_params.get('use_wandb'):
            wandb.log({"Training loss": train_loss / 50})
          print(f'Train loss for fold {i}: {train_loss / 50:.3f}')
        trn_mae = self.metric.compute().item()
        self.metric.reset()
        evaluate(m, dl_val, self.metric, self.device)
        val_mae = self.metric.compute().item()
        if wandb_params.get('use_wandb'):
          wandb.log({"Eval loss": val_mae})
        print(f'Eval loss for fold {i}: {val_mae:.3f}')
        self.metric.reset()
        pbar.set_postfix(trn_loss=trn_mae, val_loss=val_mae)
      metrics['trn_mae'].append(trn_mae)
      metrics['val_mae'].append(val_mae)
    return pd.DataFrame(metrics)

def get_args_parser(add_help=True):
  import argparse
  
  parser = argparse.ArgumentParser(description="Pytorch K-fold Cross Validation", add_help=add_help)
  parser.add_argument("-c", "--config", default="./config.py", type=str, help="configuration file")

  return parser

if __name__ == "__main__":
  args = get_args_parser().parse_args()
  
  exec(open(args.config).read())
  cfg = config
  train_params = cfg.get('train_params')
  device = train_params.get('device')
  wandb_params = cfg.get("wandb")

  if wandb_params.get('use_wandb'):
    wandb.init(project='Jeju_traffic_prediction')
    # 실행 이름 설정
    wandb.run.name = wandb_params.get('wandb_runname')
    wandb.run.save()
    wandb.config.update(cfg)

  files = cfg.get('files')
  X_df = pd.read_csv(files.get('X_csv'), index_col=0).to_numpy(dtype=np.float32)
  y_df = pd.read_csv(files.get('y_csv'), index_col=0).to_numpy(dtype=np.float32)

  Model = cfg.get('model')

  if not issubclass(Model, nn.Module):
    print("Select Ml model from sklearn...")
    y_df = y_df.ravel()
    model_params = cfg.get('ml_model_params')
    res = ml_kfold(Model, X_df, y_df, model_params)

    res = pd.concat([res, res.apply(['mean', 'std'])])
    print(res)
    res.to_csv(files.get('output_csv'))

  else:
    print("Select ANN model with Pytorch...")
    X, y = torch.tensor(X_df), torch.tensor(y_df)

    model_params = cfg.get('ann_model_params')
    model_params['input_dim'] = X.shape[-1]
    
    
    dl_params = train_params.get('data_loader_params')

    Optim = train_params.get('optim')
    optim_params = train_params.get('optim_params')

    metric = train_params.get('metric').to(device)
    
    cv = KFoldCV(X, y, Model, model_kwargs=model_params,
                epochs=train_params.get('epochs'),
                criterion=train_params.get('loss'),
                Optimizer=Optim,
                optim_kwargs=optim_params,
                trn_dl_kwargs=dl_params, val_dl_kwargs=dl_params,
                metric=metric,
                device=device)
    res = cv.run()

    res = pd.concat([res, res.apply(['mean', 'std'])])
    print(res)
    res.to_csv(files.get('output_csv'))
