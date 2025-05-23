from helperFunctions import *
from netDefinition import *
import argparse
import numpy as np
import torch
import torch.optim as optim
import torch.nn as nn
import torch.nn.functional as F
import time
import pdb
from torch.autograd import Variable
from multiprocessing import freeze_support
from torch.utils.data import TensorDataset, DataLoader

# Helper grouping classes.
class directory:
    pass
class filename:
    pass
class I:
    pass    
class N:
    pass

def save_net():
    net.eval()
    torch.save(net, 'endTrain_savedNet')

def save_vals_accuracies():
    np.save('endTrain_valSoftPredictions', valSoftPredictions)
    np.save('endTrain_valTargets', valTargets)
    np.save('endTrain_accuracies', accuracies)

def save_loss_vectors():
    np.save('endTrain_lossVector', lossVector)
    np.save('endTrain_ta_lossVector', ta_lossVector)
    np.save('endTrain_va_lossVector', va_lossVector)

def main():
    parser = argparse.ArgumentParser(description='Settings')
    parser.add_argument('-dataDirProcessed', dest='dataDirProcessed', required=True, type=str)
    parser.add_argument('-g', dest='gpuFlag', default='0')
    parser.add_argument('-dp', dest='dataParallel', default='0')
    parser.add_argument('-cpu', dest='cpuFlag', default=0, type=int,
                        help='Set to 1 to force running on CPU')
    parser.add_argument('-e', dest='epochs', default=10, type=int)
    parser.add_argument('-lr', dest='learningRate', default=5e-5, type=float)
    parser.add_argument('-L2', dest='l2_weightDecay', default=0.001, type=float)
    parser.add_argument('-mb', dest='minibatchSize', default=16, type=int)
    parser.add_argument('-s', dest='savingOptions', default=3)
    parser.add_argument('-dnn', dest='dnnArchitecture', default='inceptionModuleV1_108x108')
    parser.add_argument('-nils', dest='numberOfInceptionLayers', default=-1, type=int)
    args = parser.parse_args()

    directory.loadDataFrom = args.dataDirProcessed
    N.minibatchSize = int(args.minibatchSize)
    N.runValidation = 50
    N.trainingAccuracySamples = 100

    if N.minibatchSize % 2 != 0:
        raise ValueError('Minibatch size must be even.')  

    global net
    net = Net(args.dnnArchitecture, w_init_scheme='He', bias_inits=1.0, incep_layers=args.numberOfInceptionLayers)

    if args.cpuFlag != 1:
        if args.gpuFlag != '0':
            torch.cuda.set_device(0)
            net.cuda()
        if args.dataParallel == '1':
            net = torch.nn.DataParallel(net, device_ids=[0,2])

    optimizer = optim.Adam(net.parameters(), lr=float(args.learningRate),
                           weight_decay=float(args.l2_weightDecay))

    # Load training and validation data
    tTrainingDataPos = torch.load(directory.loadDataFrom + 'tTrainingDataPos')
    tTrainingDataNeg = torch.load(directory.loadDataFrom + 'tTrainingDataNeg')
    tValData = torch.load(directory.loadDataFrom + 'tValData')
    tValLabels = torch.load(directory.loadDataFrom + 'tValLabels')
    tTrainingLabelsPos = torch.ones(tTrainingDataPos.size(0)).long()
    tTrainingLabelsNeg = torch.zeros(tTrainingDataNeg.size(0)).long()

    dataSetPos = TensorDataset(tTrainingDataPos, tTrainingLabelsPos)
    dataSetNeg = TensorDataset(tTrainingDataNeg, tTrainingLabelsNeg)
    dataVal = TensorDataset(tValData, tValLabels)

    positiveDataLoader = DataLoader(dataSetPos, batch_size=N.minibatchSize//2, shuffle=True, num_workers=2)
    negativeDataLoader = DataLoader(dataSetNeg, batch_size=N.minibatchSize//2, shuffle=True, num_workers=2)
    validationDataLoader = DataLoader(dataVal, batch_size=N.minibatchSize//2, shuffle=False, num_workers=2)
    #ins_positiveDataLoader = DataLoader(dataSetPos, batch_size=N.minibatchSize//2, shuffle=False, num_workers=2)
    #ins_negativeDataLoader = DataLoader(dataSetNeg, batch_size=N.minibatchSize//2, shuffle=False, num_workers=2)
    ins_positiveDataLoader = DataLoader(dataSetPos, batch_size=N.minibatchSize//2, shuffle=False, num_workers=2)
    ins_negativeDataLoader = DataLoader(dataSetNeg, batch_size=N.minibatchSize//2, shuffle=False, num_workers=2)

    def get_balanced_batch(pos_data, pos_labels, neg_data, neg_labels, batch_size):
        n_pos = min(batch_size // 2, len(pos_data))
        n_neg = min(batch_size - n_pos, len(neg_data))  # Handles partial batches
        
        if n_pos == 0 or n_neg == 0:
            raise ValueError("Insufficient data for balanced batch")
            
        pos_idx = torch.randperm(len(pos_data))[:n_pos]
        neg_idx = torch.randperm(len(neg_data))[:n_neg]
        
        return (
            torch.cat([pos_data[pos_idx], neg_data[neg_idx]]),
            torch.cat([pos_labels[pos_idx], neg_labels[neg_idx]])
        )

    N.totalTrainingSamples = tTrainingDataPos.size(0) + tTrainingDataNeg.size(0)
    N.epochs = int(args.epochs)
    N.miniBatchesPerEpoch = int(round(N.totalTrainingSamples / float(N.minibatchSize)))
    global accuracies, lossVector, ta_lossVector, va_lossVector, valSoftPredictions, valTargets
    accuracies = np.zeros((2, 0), dtype=np.float32)
    lossVector = np.zeros((1, 0), dtype=np.float32)
    ta_lossVector = np.zeros((1, 0), dtype=np.float32)
    va_lossVector = np.zeros((1, 0), dtype=np.float32)
    maxValAccuracy = 0.0
    sm = nn.Softmax(dim=1)

    for epoch in range(N.epochs):
        instantaneousLoss = 0.0
        accumulatedLoss = 0.0
        posIterator = iter(positiveDataLoader)
        negIterator = iter(negativeDataLoader)
        net.train()

        for bb in range(N.miniBatchesPerEpoch):
            startTime = time.time()
            try:
                pos_batch, pos_labels = next(posIterator)
                neg_batch, neg_labels = next(negIterator)
                currentBatchData, currentBatchLabels = get_balanced_batch(
                    pos_batch, pos_labels, neg_batch, neg_labels, N.minibatchSize
                )
            except StopIteration:
                print("End of epoch - resetting iterators")
                posIterator = iter(positiveDataLoader)
                negIterator = iter(negativeDataLoader)
                continue  # Skip to next iteration
            except ValueError as e:
                print(f"Skipping batch due to: {str(e)}")
                continue  # Skip to next iteration

            if args.gpuFlag != '0' and args.cpuFlag != 1:
                currentBatchData = currentBatchData.cuda()
                currentBatchLabels = currentBatchLabels.cuda()
            
            optimizer.zero_grad()
            yEst = net(currentBatchData)
            loss = F.cross_entropy(yEst, currentBatchLabels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), max_norm=1.0)
            optimizer.step()
            
            instantaneousLoss = loss.item()
            accumulatedLoss += instantaneousLoss
            lossVector = np.append(lossVector, np.array([[instantaneousLoss]]), axis=1).astype(np.float32)
            
            if bb % N.runValidation == 0:
                accumulatedLoss /= N.runValidation
                net.eval()
                trainingCorrect = 0.0
                trainingTotal = 0.0
                taLoss = 0.0
                iterationAccumulator = 0.0
                ta_pIter = iter(ins_positiveDataLoader)
                ta_nIter = iter(ins_negativeDataLoader)
                for tt in range(N.trainingAccuracySamples):
                    try:
                        ta_posData, ta_posLabels = next(ta_pIter)
                    except StopIteration:
                        break
                    ta_negData, ta_negLabels = next(ta_nIter)
                    ta_totalData = torch.cat((ta_posData, ta_negData), 0)
                    ta_totalLabels = torch.cat((ta_posLabels, ta_negLabels), 0)
                    if args.gpuFlag != '0' and args.cpuFlag != 1:
                        ta_totalData, ta_totalLabels = ta_totalData.cuda(), ta_totalLabels.cuda()
                    yEst_ta = net(ta_totalData)
                    ta_softPredictions = sm(yEst_ta)[:, 1]
                    taLoss += F.cross_entropy(yEst_ta, ta_totalLabels).item()
                    trainingCorrect += ((ta_softPredictions.cpu().data.numpy() > 0.5) ==
                                        ta_totalLabels.cpu().data.numpy()).sum()
                    trainingTotal += ta_totalLabels.size(0)
                    iterationAccumulator += 1
                if iterationAccumulator > 0:
                    taLoss /= iterationAccumulator
                ta_lossVector = np.append(ta_lossVector, np.array([[taLoss]]), axis=1).astype(np.float32)
                trainingAccuracy = 100.0 * trainingCorrect / float(trainingTotal)
                
                valSoftPredictions, valTargets, vaLoss = extractForwardPropResults_binary(net, validationDataLoader, gpuFlag=args.gpuFlag)
                valAccuracy = 100.0 * np.sum((valSoftPredictions > 0.5) == valTargets) / valTargets.shape[0]
                va_lossVector = np.append(va_lossVector, np.array([[vaLoss]]), axis=1).astype(np.float32)
                accuracies = np.append(accuracies, [[trainingAccuracy], [valAccuracy]], axis=1)
                print('[%d, %5d] [Ins Training loss: %.3f] [Accum Training loss: %.3f] [TA Training loss: %.3f] [Val loss: %.3f] [Training Accuracy: %.2f] [Val Accuracy: %.2f]' %
                      (epoch+1, bb+1, instantaneousLoss, accumulatedLoss, taLoss, vaLoss, trainingAccuracy, valAccuracy))
                
                # Early stopping check (add after validation logging)
                if epoch > 5 and np.mean(accuracies[1, -5:]) < maxValAccuracy - 1.0:
                    print(f"Early stopping at epoch {epoch} - val accuracy plateaued")
                    break

                if args.savingOptions == '2':
                    save_net()
                    save_vals_accuracies()
                    save_loss_vectors()
                elif args.savingOptions == '3':
                    save_loss_vectors()
                    save_vals_accuracies()
                    if valAccuracy > maxValAccuracy:
                        save_net()
                        maxValAccuracy = valAccuracy
                
                endTime = time.time()
                totalTime = endTime - startTime
                print("Time: {:.2f}".format(totalTime))
                net.train()

    print('Finished Training')

    if args.gpuFlag != '0':
        torch.cuda.empty_cache()  # Clean up GPU memory

    if args.savingOptions in ['1','2']:
        save_loss_vectors()
        valSoftPredictions, valTargets, _ = extractForwardPropResults_binary(net, validationDataLoader, gpuFlag=args.gpuFlag)
        save_net()
        save_vals_accuracies()

    # --- Evaluate on the test set and save test predictions ---
    try:
        tTestData = torch.load(directory.loadDataFrom + 'tTestData')
        tTestLabels = torch.load(directory.loadDataFrom + 'tTestLabels')
        testDataset = TensorDataset(tTestData, tTestLabels)
        testDataLoader = DataLoader(testDataset, batch_size=N.minibatchSize//2, shuffle=False, num_workers=2)
        testSoftPredictions, testTargets, testLoss = extractForwardPropResults_binary(net, testDataLoader, gpuFlag=args.gpuFlag)
        np.save('endTrain_testSoftPredictions.npy', testSoftPredictions)
        np.save('endTrain_testTargets.npy', testTargets)
        print("Test predictions saved successfully.")
    except Exception as e:
        print("Test set evaluation failed: ", e)

    print("FIN")
    #pdb.set_trace()

if __name__ == '__main__':
    freeze_support()
    main()
