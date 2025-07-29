<h1>
  <img src="OpenFPL.png" alt="OpenFPL" width="100" style="vertical-align: middle; margin-right: 10px;">
  OpenFPL
</h1>
<b>The accurate openly available forecasting method for Fantasy Premier League</b>

<hr>

<i>Want to support this initiative?</i>

<a href="https://www.paypal.com/donate/?hosted_button_id=EKNGVA5RU2B96" target="_blank">
  <img src="https://www.paypalobjects.com/en_US/i/btn/btn_donate_SM.gif" alt="Support" style="height: 24px; margin-left: 8px;">
</a>

<hr>

## Get started

### 1. Plug

With [Python](https://www.python.org/downloads/) preinstalled, run: ```pip install -r plug.txt```

### 2. Play

Open and run *play.ipynb* for OpenFPL predictions on sample data

## Custom data

To use OpenFPL on custom data, you need to construct samples based on data from FPL and Understat APIs (see *data/samples.csv* and [paper](https://arxiv.org/abs/2507.XXXXX) for inspiration):

- [FPL API](https://fantasy.premierleague.com/api/bootstrap-static/)
- [Understat API](https://understat.com/league/EPL/)

Historical FPL and Understat data can be accessed by help of [FPL Historical Dataset](https://github.com/vaastav/Fantasy-Premier-League)

## Head-to-head evaluation with state-of-the-art commercial method

| Method | RMSE<sub>Zeros*</sub> | RMSE<sub>Blanks*</sub> | RMSE<sub>Tickers*</sub> | RMSE<sub>Haulers*</sub> |
| :--  | --- | --- | --- | --- |
| OpenFPL | 0.818 | 1.291 | <b>1.517</b> | <b>5.142</b> |
| [FPL Review Massive Data Model](https://fplreview.com/) | <b>0.689</b> | <b>1.189</b> | 1.594 | 5.172 | 

<sup>*</sup> *Zeros*: Non-playing and 0 FPL points, *Blanks*: ≤ 2 FPL points, *Tickers*: 3 or 4 FPL points, *Haulers*: ≥ 5 FPL points

## Resources

- Scientific paper - [OpenFPL: An open-source forecasting method rivaling state-of-the-art Fantasy Premier League services](https://arxiv.org/abs/2507.XXXXX)
- Model search framework - [*K*-Best Search](https://github.com/daniegr/KBestSearch)

## Citation

Should you find the work helpful in your research, please cite the following:
```
@article{groos2025openfpl,
  title={OpenFPL: An open-source forecasting method rivaling state-of-the-art Fantasy Premier League services},
  author={Groos, Daniel},
  year={2025},
  publisher={arXiv}
}
```
